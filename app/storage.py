"""物件儲存(MinIO / S3 相容)。

🔴 拓撲前提:Cats 平台只有 `portal-gateway` 持有 80/443,MinIO 待在 `backend` 網路、
不上 `cats-edge`、不對主機發布 port。**瀏覽器不直連物件儲存**,所以上傳與下載一律
由本服務串流代收代送(presigned 直傳留待日後另開 gateway 路由時再議)。

上傳採 S3 multipart:邊收邊送、邊算 SHA-256,記憶體用量固定在一個 chunk,
不在容器內落暫存檔(平台規約:容器內不寫檔當狀態)。
"""

import hashlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import aioboto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .config import Settings

# S3 規定:multipart 的每個 part(最後一個除外)不得小於 5 MiB。
MIN_PART_BYTES = 5 * 1024 * 1024


class StorageError(RuntimeError):
    pass


class TooLarge(StorageError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"內容超過上限 {limit} bytes")
        self.limit = limit


@dataclass(slots=True)
class UploadResult:
    size_bytes: int
    sha256: str
    head: bytes


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = aioboto3.Session()
        self._bucket = settings.s3_bucket
        self._part_size = max(settings.s3_multipart_chunk_bytes, MIN_PART_BYTES)

    def _client(self):
        return self._session.client(
            "s3",
            endpoint_url=self._settings.s3_endpoint_url,
            region_name=self._settings.s3_region,
            aws_access_key_id=self._settings.s3_access_key,
            aws_secret_access_key=self._settings.s3_secret_key,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},  # MinIO 走 path style
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=60,
            ),
        )

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
                    raise
                await s3.create_bucket(Bucket=self._bucket)

    async def check_ready(self) -> None:
        """/ready 用:確認物件儲存可達。"""
        async with self._client() as s3:
            await s3.head_bucket(Bucket=self._bucket)

    async def upload_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        max_bytes: int,
        on_head: Callable[[bytes], None] | None = None,
    ) -> UploadResult:
        """把 async 位元組流寫進物件儲存。

        `on_head` 會在**開始寫任何資料前**以檔頭呼叫一次(magic bytes 判型用);
        它拋出例外即中止,此時不會產生任何物件。
        """
        sniff_len = self._settings.magic_sniff_bytes
        digest = hashlib.sha256()
        total = 0
        head = b""
        buffer = bytearray()
        head_checked = False

        async with self._client() as s3:
            upload_id: str | None = None
            parts: list[dict] = []
            try:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise TooLarge(max_bytes)
                    digest.update(chunk)
                    buffer.extend(chunk)

                    if not head_checked and len(buffer) >= sniff_len:
                        head = bytes(buffer[:sniff_len])
                        if on_head is not None:
                            on_head(head)  # 判型不過就在這裡中止,尚未寫出任何 part
                        head_checked = True

                    while len(buffer) >= self._part_size:
                        if upload_id is None:
                            resp = await s3.create_multipart_upload(Bucket=self._bucket, Key=key)
                            upload_id = resp["UploadId"]
                        part_body = bytes(buffer[: self._part_size])
                        del buffer[: self._part_size]
                        parts.append(
                            await self._put_part(s3, key, upload_id, len(parts) + 1, part_body)
                        )

                # 檔案比 sniff_len 還小的情況,收完才判型。
                if not head_checked:
                    head = bytes(buffer[:sniff_len])
                    if on_head is not None:
                        on_head(head)

                if upload_id is None:
                    await s3.put_object(Bucket=self._bucket, Key=key, Body=bytes(buffer))
                else:
                    if buffer:
                        parts.append(
                            await self._put_part(s3, key, upload_id, len(parts) + 1, bytes(buffer))
                        )
                    await s3.complete_multipart_upload(
                        Bucket=self._bucket,
                        Key=key,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                    )
            except BaseException:
                if upload_id is not None:
                    # 沒 abort 的話碎片會一直佔空間。
                    try:
                        await s3.abort_multipart_upload(
                            Bucket=self._bucket, Key=key, UploadId=upload_id
                        )
                    except ClientError:
                        pass
                raise

        return UploadResult(size_bytes=total, sha256=digest.hexdigest(), head=head)

    async def _put_part(self, s3, key: str, upload_id: str, number: int, body: bytes) -> dict:
        resp = await s3.upload_part(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
            PartNumber=number,
            Body=body,
        )
        return {"ETag": resp["ETag"], "PartNumber": number}

    async def iter_object(self, key: str) -> AsyncIterator[bytes]:
        """串流讀出物件內容(下載用)。"""
        async with self._client() as s3:
            resp = await s3.get_object(Bucket=self._bucket, Key=key)
            async for chunk in resp["Body"].iter_chunks(chunk_size=1024 * 1024):
                yield chunk

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)

    async def delete_prefix(self, prefix: str) -> None:
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if keys:
                    await s3.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
