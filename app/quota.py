"""專案容量級距政策(F17 / T49)。

**級距代號 → 位元組上限的對應只存在這個模組**,而不是複製到每一列資料。
這樣把標準級距從 2 GB 調成 3 GB 只需要改設定值,不必 UPDATE 全表,
也不會出現「分不清哪些列是政策預設、哪些是個案調整」的遷移難題。

超限時的錯誤訊息也放在這裡,理由同上:預檢(有 Content-Length)與收完後檢查
是兩條不同的程式路徑,訊息若各寫一份必然會漂移。
"""

from . import problems
from .config import Settings
from .models import Project, QuotaTier


def limit_for(settings: Settings, tier: QuotaTier) -> int:
    """回傳該級距的專案容量上限(bytes)。

    參數:settings 設定值、tier 級距代號。
    副作用:無。
    """
    if tier is QuotaTier.extended:
        return settings.max_project_extended_bytes
    return settings.max_project_bytes


def project_limit(settings: Settings, project: Project) -> int:
    """回傳指定專案目前適用的容量上限(bytes)。副作用:無。"""
    return limit_for(settings, project.quota_tier)


def over_quota(settings: Settings, project: Project, incoming: int) -> bool:
    """加上 `incoming` bytes 之後會不會超過本專案的容量上限。副作用:無。"""
    return project.total_bytes + incoming > project_limit(settings, project)


def too_large(settings: Settings, project: Project, incoming: int) -> problems.ProblemError:
    """組出超限的 413 錯誤(RFC 7807)。

    F17 明文要求「不能只丟一句 Payload Too Large」:detail 要含目前級距、上限、
    已用量與本次大小,並給出**依級距而異**的下一步指引——
    對已是最大級距的專案講「可申請擴充」是錯誤指引,會讓人去申請一個不存在的東西。

    數值同時以 RFC 7807 的擴充成員帶出,前端才能直接畫用量條,
    而不必去 parse 中文句子。

    參數:settings、project(讀 quota_tier 與 total_bytes)、incoming 本次要寫入的位元組數。
    回傳:可直接 `raise` 的 ProblemError。副作用:無。
    """
    tier = project.quota_tier
    limit = limit_for(settings, tier)
    if tier is QuotaTier.standard:
        guidance = (
            "如需更多空間,可向平台管理員申請擴充級距"
            f"({limit_for(settings, QuotaTier.extended)} bytes)。"
        )
    else:
        guidance = "本專案已是最大級距;請刪除不再需要的舊版本或檔案以釋出空間。"

    return problems.payload_too_large(
        f"專案容量不足:目前級距 {tier.value}、上限 {limit} bytes、"
        f"已用 {project.total_bytes} bytes、本次 {incoming} bytes。{guidance}",
        quota_tier=tier.value,
        quota_bytes=limit,
        used_bytes=project.total_bytes,
        incoming_bytes=incoming,
    )
