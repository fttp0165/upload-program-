# CI 改跑 VM self-hosted runner(額度事件的解法)

**建立日期:** 2026-07-31 10:00
**最後更新:** 2026-07-31 12:00
**版本:** v1.1
**對應任務:** 無編號(基礎設施;承接 2026-07-29 v0.1.2 發版中斷)

---

## 計畫與結果(小任務,一次記錄)

**背景:** GitHub Actions 免費額度用罄,v0.1.2 起所有 push run「秒殺無 runner」,
出貨全卡。Benny 裁示:在 VM 上跑 CI。

**做法:** VM 註冊為 repo 的 **self-hosted runner**(自架 runner 不計分鐘額度);
`ci.yml` 三個 job 的 `runs-on` 改 `[self-hosted, Linux, X64]`。

**🔴 紅線核對:** 「正式機不 build 部署」不受影響——image 仍推 GHCR、
部署仍是改 tag 後 pull;變的只是**建置在哪台機器發生**。
runner 是常駐服務裝在共用 VM,**應知會 portal**(平台資產禮貌,非契約義務)。

**風險與緩解:**
1. 共用 VM 資源競爭(docker build + Trivy 吃 CPU/磁碟)——單 runner 天生序列化,
   首次全量跑建議低峰;磁碟留意 runner 工作目錄與 build cache
2. 冒煙步驟會佔用主機 port 18080——與現有服務無衝突(DB/MinIO 不曝 port)
3. 私有 repo 才可用 self-hosted(公開 repo 會被陌生 PR 執行任意程式)——本 repo 私有 ✅
4. 額度恢復想切回:`runs-on: ubuntu-latest` 一行改回即可(註解已載明)

**對現有資料的影響:** 🟢 純 CI 設定。**回滾:** git revert + 移除 runner 服務。


---

## 結果補記(2026-07-31 12:00):run #87 全綠,洋蔥共五層

**根因只有一個:VM 對外網路是白名單制**(GitHub API/git、PyPI、mirror.gcr.io、
ghcr.io 可達;docker.io、Debian apt 庫、GitHub Releases 資產域
objects.githubusercontent.com、Actions blob storage 不可達)。
CI 每個外部依賴逐一改道:

| Run | 死因 | 修法 |
|---|---|---|
| #81 | `setup-python` 下載工具鏈 | 改 VM 系統 Python + venv |
| #82 | buildx docker-container driver 不吃 dockerd 的 registry mirror,直連 docker.io 逾時 | 改 dockerd 內建 builder(`docker build`);GHA 快取一併移除(其後端也不可達) |
| #83 | (自身失誤:修正腳本做了替換但漏寫回檔案) | 補寫入,**以 grep 驗證檔案內容後才 commit** |
| #85 | Dockerfile `apt-get build-essential` 連不到 apt 庫 | 移除 apt——相依全有 wheel,從未真的編譯;`--only-binary=:all:` 把假設變強制 |
| #86 | trivy-action 從 Releases 資產域下載 binary(exit 28) | 改官方**容器版** Trivy(image 走 mirror、DB 走 ghcr,兩路已證實可達) |
| **#87** | — | **測試(353)→ build → 大小/non-root/health 冒煙 → Trivy 全綠** |

### 教訓

1. **白名單網路裡,每個「action 幫你下載東西」的步驟都是地雷**——工具鏈、
   builder image、apt、掃描器 binary,全要改走已證實可達的路(系統資源、
   dockerd mirror、ghcr、PyPI)。
2. 修 CI 檔的腳本,**寫完要驗檔案內容再 commit**(#83 的教訓——assert 通過
   不代表寫入了)。
3. 意外收穫:image 建置比雲端更乾淨(不再依賴 apt)且同機層快取天然存在。

### 遺留

- runner 以 `run.sh` 前景模式跑著——**要改裝 systemd 服務**(`svc.sh install deploy`),
  否則關終端就停
- runner 目錄在 `/opt/upload-program/actions-runner`,建議日後搬 `/home/deploy`
- 知會 portal:共用 VM 上多了一個常駐 runner 服務
