# 申請:CI 容器網路開通 Debian 套件庫(正式化 apt 存取)

**From(發文專案):** upload-program 維運(負責人 Benny)
**To(收文專案):**   cats-portal 維運(Platform)
**發文日期時間:**   2026-08-26 15:50 (UTC+8)——檔名時戳 `202608260750` 為 UTC,同一時刻
**回覆對象:**       無(主動發文;緣起本專案 T93,2026-08-24)

**建立日期:** 2026-08-26 15:50
**最後更新:** 2026-08-26 15:50
**版本:** v1.0

---

## 0. 一句話請求

請把「**CI 容器(VM self-hosted runner 上的 `docker build`)→ Debian 官方套件庫**」
這條網路路徑**正式化**:確認它是政策、列入 allowlist 文件,而不是像現在——
**能用,但沒人說得出為什麼能用、也沒人保證明天還能用**。

## 1. 申請項目(依序裁示)

| # | 項目 | 性質 |
|---|---|---|
| 1 | **確認現況**:VM 上 CI 容器目前可連 `deb.debian.org`(2026-08-24 實測 `APT_OK`),這是**平台網路政策**,還是**未登記的開放**?2026-07-30 同一條路是不通的(我方 CI run #85 實測),期間我方未收到任何變更通知 | 詢問 |
| 2 | 若第 1 項答案是「政策」:請將 `deb.debian.org`、`security.debian.org`(80/443)列入**書面 allowlist**(《App服務對齊指南》或平台網路政策文件),讓後續變更走公告(如同 gateway 變更通知的慣例) | 申請 |
| 3 | **建議項(非必要)**:平台提供 apt cache/mirror(如 `apt-cacher-ng` 容器,比照現行 dockerd 走 `mirror.gcr.io` 的安排),各 App 的 build 統一走它 | 建議 |

## 2. 為什麼需要(不是「有比較方便」,是修補鏈踩在上面)

1. **base image 的 OS 套件 CVE,目前唯一修補路徑就是 build 時 apt 升級。**
   2026-08-24(T93)`python:3.12-slim` 帶出 util-linux 四支 CVE(2026-53612~53615,
   HIGH ×36),上游 image 尚未重建;本專案散布可執行檔,Trivy
   `HIGH,CRITICAL --exit-code 1` 是自我加嚴紅線,`.trivyignore` 放行已明文拒絕(T82)。
   修法只剩 Dockerfile runtime 層 `apt-get --only-upgrade`——**它需要連 Debian 套件庫**。
2. **這條路已經無聲變動過一次。** 07-30 不通(builder 的 apt 因此整段移除)、
   08-24 變通(`APT_OK`)。方向恰好是「變好」,所以沒炸;哪天反向再變一次,
   CI 會紅在與當批改動無關的地方,再考古一輪。**可用性沒有契約,就只是運氣。**
3. **CDN 節點不一致已造成過無聲失敗。** 08-24 實測:同一 commit 相隔 4 秒的兩次
   build,一次抽到未同步 security 索引的節點——`apt-get` 回 0 **而什麼都沒升級**;
   若非我方 Dockerfile 自帶版本驗證,一個未修補的 image 會帶著全綠 CI 推上 GHCR。
   現以「版本判準 + 重試三次」workaround;**平台級 mirror(申請項 3)可根治**,
   並讓全 VM 的 apt 流量不重複打外網。

## 3. 事實時間線

| 時間(UTC+8) | 事實 | 出處 |
|---|---|---|
| 2026-07-30 | CI run #85:builder `apt-get` 不可達,炸;apt 整段移除 | Dockerfile 檔頭紀錄 |
| 2026-08-11 | main 最後一次全綠(run #173) | T93 |
| 2026-08-24 14:35 | Trivy 紅:base image util-linux CVE ×36 | T93 |
| 2026-08-24 | CI 診斷實測 **`APT_OK`**(`deb.debian.org/debian-security trixie-security` 可達)——與 07-30 相反,未收到變更通知 | T93 診斷步驟 |
| 2026-08-24 15:22 | 同 commit 兩 run 相隔 4 秒結果相反(CDN 索引不一致);版本驗證擋下無聲失敗 | T93 第五輪 |
| 2026-08-24 15:33 | 加重試迴圈後 CI 綠;此後修補鏈**常態依賴** apt 可達 | T93 收尾 |

## 4. 影響範圍(給裁示者的邊界)

- **只涉及 CI build**(self-hosted runner 於 Cats VM 上的 `docker build`)。
- **runtime 容器不需要、也不申請**對外連 apt——正式運行的容器維持現狀。
- 不涉及 gateway、路由、`cats-edge`、任何正式站行為;🟢 對現有服務與資料無影響。
- 若裁示為「不開放」:請一併告知,我方改走「等上游重建 + 期間不發版」或
  「換 base image tag」路線(T93 已列),並把 Dockerfile 的 apt 層移除——
  **兩種答案都能執行,不能執行的是「不知道答案」。**

## 5. 需要 cats-portal 回覆的三件事

1. 申請項 1:現況是政策還是偶然?(07-30 → 08-24 之間發生了什麼變更?)
2. 申請項 2:可否列入書面 allowlist,後續變更走公告?
3. 申請項 3(建議):是否評估平台級 apt cache/mirror?不做也請回個「不做」,
   我方即保留現行重試 workaround 並停止等待。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-08-26 | Claude(Benny 授權) | 初版:三項申請(確認政策 / allowlist 文件化 / 建議 mirror),緣起 T93 |
