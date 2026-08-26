# 申請:開通 CI runner 容器網路對 Debian 套件庫的連線

**專案:** upload-program
**發文專案:** upload-program
**受文專案:** cats-portal
**發文時間:** 2026-08-26 07:50
**建立日期:** 2026-08-26 07:50
**最後更新:** 2026-08-26 07:50
**版本:** v1.0
**申請標的:** Cats 共用 VM 上 self-hosted runner 的**容器建置網路**egress 白名單
**目前狀態:** 🔴 **本服務的發版已被此問題阻塞**(v0.3.0 推不上 GHCR)

---

## 1. 一句話

`docker build` 期間容器連不到 Debian 套件庫,導致我方**無法修補 base image 的
OS 層 CVE**;Trivy 依規約對 HIGH 即失敗,發版因此停住。
請求開通 `deb.debian.org`(或提供公司內部 Debian 鏡像的位址)。

## 2. 現場問題

### 2.1 觸發事件

2026-08-26,本專案 PR #33(v0.3.0)的 CI:

| job | 結果 |
|---|---|
| 測試 / lint / 文件同步 | ✅ 綠(559 passed) |
| **Build image + Trivy 掃描** | ❌ **紅** |
| Push GHCR(僅版本 tag) | ⏭ 因 `needs: build` 而跳過 |

Trivy 報告:

```
upload-program:ci (debian 13.6)
Total: 3 (HIGH: 3, CRITICAL: 0)

libssl3t64              CVE-2026-14456  HIGH  fixed  3.5.6-1~deb13u2 → 3.5.7-1~deb13u2
openssl                 CVE-2026-14456  HIGH  fixed  3.5.6-1~deb13u2 → 3.5.7-1~deb13u2
openssl-provider-legacy CVE-2026-14456  HIGH  fixed  3.5.6-1~deb13u2 → 3.5.7-1~deb13u2
```

三筆是**同一個來源套件(openssl)的三個二進位套件**,同一個 CVE。

### 2.2 這不是我方程式碼的問題

我方 image 的 runtime stage **從頭到尾沒有任何 `apt-get`**,OS 套件原封不動
來自官方 `python:3.12-slim`。同一時間 `main` 分支建置也會紅——
**與任何一次程式碼變更無關**。

Debian 已經發布修好的 `3.5.7-1~deb13u2`,但官方 image 尚未重建併入。
我方的建置指令已帶 `--pull`(確保取到上游同 tag 的最新一份),
但**上游沒動,我們就沒得取**。

### 2.3 我方嘗試過的修法,以及它為什麼失敗

在 runtime stage 加一段只升 openssl 家族的 apt:

```
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade \
       libssl3t64 openssl openssl-provider-legacy \
    && rm -rf /var/lib/apt/lists/*
```

CI 實測結果(run 32940513060,第 9 層原始輸出):

```
#9 30.66 Ign:1 http://deb.debian.org/debian trixie InRelease
#9 30.66 Ign:2 http://deb.debian.org/debian trixie-updates InRelease
#9 30.66 Ign:3 http://deb.debian.org/debian-security trixie-security InRelease
#9 37.67 Err:1 http://deb.debian.org/debian trixie InRelease
#9 37.67   Could not connect to debian.map.fastlydns.net:80 (199.232.114.132), connection timed out
#9 37.70 W: Some index files failed to download. They have been ignored, or old ones used instead.
#9 37.73 libssl3t64 is already the newest version (3.5.6-1~deb13u2).
#9 37.73 0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
#9 DONE 37.7s
```

**三個 suite 全部 connection timed out。** 已回退該修改。

> ⚠️ **順帶一提,供貴方參考(與本申請無關但值得傳出去):**
> `apt-get update` 抓不到索引時**只印 `W:`、exit code 仍是 0**。
> 後續的 `--only-upgrade` 因此拿 base image 裡的**舊索引**比對,回報
> 「已經是最新版」,整條 `&&` 鏈**成功**——建置產出一個**看起來已修補、
> 實際一個位元組都沒動**的 image。
> 我方是靠 Trivy 仍然紅燈才發現的。**任何在受限網路裡跑 `apt-get update`
> 的 Dockerfile 都可能有這個問題**,平台上其他服務值得檢查一下。

### 2.4 這是第三次了

| 時間 | 被擋的東西 | 當時的處置 |
|---|---|---|
| 2026-07-30(run #85) | builder stage 的 `apt-get`(build-essential) | 把 apt **整個拿掉**(改用 manylinux wheel,`--only-binary=:all:`) |
| 2026-08-12(T96) | base image 的 `util-linux` 家族 CVE | 加 `--pull`;當時**運氣好**,上游已重建 |
| **2026-08-26(本次)** | base image 的 `openssl` CVE | ❌ 無解——上游未重建,apt 又不通 |

第一次繞掉了(那次繞得對:相依本來就有 wheel,不需要編譯器)。
第二次是上游剛好已經重建。**第三次沒有運氣了。**

## 3. 申請內容

請求下列**其一**即可(貴方擇一,我方配合):

| 選項 | 內容 |
|---|---|
| **A** | 開通 Cats VM 上 **`docker build` 使用的網路**對 `deb.debian.org` / `security.debian.org` 的 **egress**(80/443) |
| **B** | 提供**公司內部 Debian 鏡像**的位址,我方在 Dockerfile 內改寫 `sources` 指向它 |

**我方偏好 B**,若貴方已有內部鏡像的話:egress 面更小、對外相依更少,
而且鏡像的更新節奏由公司自己掌握。若沒有內部鏡像則走 A。

### 3.1 範圍(這不是「開通對外網路」的一般性請求)

- **只在建置期**需要,執行期的容器**不需要**、也不應該有這條路。
- **只要 Debian 官方套件庫**兩個網域,不需要 PyPI、npm 或任何其他來源
  (Python 相依走 `requirements.txt` + wheel,已經可達)。
- 我方的 Dockerfile 是 multi-stage,`apt` 只會出現在 **runtime stage 的一層**,
  且**只升不裝**(`--only-upgrade` + 明確列出套件名),不會引入新套件。

### 3.2 我方在獲准後的義務

1. `apt-get update` 的結果**自行驗證**,不再把 exit code 當成事實
   (§2.3 那個坑,我方不會再踩第二次);
2. 不做整包 `apt-get upgrade -y`——那會讓「這個 image 裝了什麼」隨建置時間漂移;
   只升 Trivy 明確點名的來源套件;
3. 上游 image 重建、該幾行變成贅生物時**主動移除**,不留長期殘留;
4. 建置期 egress 不外流到執行期 compose。

## 4. 為什麼不用其他方式解決

| 替代方案 | 為什麼不採 |
|---|---|
| **等上游 `python:3.12-slim` 重建** | ETA 未知(通常數天,無承諾)。可作為**這一次**的備援,但**不是制度**——下一個 OS 層 CVE 必然還會來,而屆時我方仍然只能等。 |
| **`.trivyignore` 記下此 CVE** | 🔴 我方已就同類情境立過**兩次**界線(T82、T96)並拒絕。本服務**散布可執行檔**給公司同仁,掃描是自我加嚴的一環,調鬆掃描是拿紅線換方便。**即使**本 CVE 幾乎確定不適用(見 §5),「我判斷用不到」不是關掉掃描的理由。 |
| **改用其他 base image** | 換 distro 影響面遠大於一個 CVE(glibc、套件名、既有三道 image 護欄全要重驗),且沒有解決「OS 層 CVE 我方無法自行修補」這個結構問題。 |

## 5. 誠實揭露:本 CVE 對我方**幾乎確定不適用**

CVE-2026-14456 是 OpenSSL **QUIC 伺服器**的記憶體無限成長(DoS)。

本服務沒有任何 QUIC 伺服器路徑:TLS 在 `portal-gateway` 終結,
容器內只有 gunicorn 的明文 HTTP;OpenSSL 只被 Python 用在**對外的 client**
連線(JWKS、MinIO)。

**我方仍然申請開通,而不是主張豁免**,理由有二:

1. 「判斷不適用」可能錯,而且會隨相依演進而失效——今天成立不代表明年成立;
2. **這次的判斷成不成立,不影響下一次**。真正的問題不是這個 CVE,
   是「我方對 OS 層 CVE 完全無能為力」。修掉那個,才不用每次都重來一遍這場對話。

## 6. 我方目前的處置與影響

- Dockerfile 已回退,**不留任何無效的 apt**(留著會讓下一個讀的人以為有在升)。
- v0.3.0 的 PR **維持開啟**,測試 job 綠、Trivy job 紅。
- 🔴 **本服務暫時無法發版**:`publish` job `needs: build`,
  Trivy 紅則 GHCR 推不上去,**tag 打了也不會有 image**。
- 線上服務**不受影響**(現行版本照常運作,本問題只影響「發下一版」)。

## 7. 貴方若要複現

> 🖥️ **在哪執行:** Cats VM(ssh)· 工作目錄任意(僅測試連線,不動任何檔案)

```bash
docker run --rm debian:trixie-slim \
  sh -c 'apt-get update; echo "exit=$?"'
```

預期(目前):三個 suite 皆 `Err: ... connection timed out`,
但最後印出 **`exit=0`**——即 §2.3 描述的行為。
開通後應改為 `Get:` / `Hit:` 且無 `Err:`。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-08-26 07:50 | Claude(Benny 裁示採「開通 apt」) | 初版:CI 容器網路連不到 Debian 套件庫,導致 OS 層 CVE 無法自行修補、發版被阻塞;附 CI 原始輸出與三次被擋的紀錄;申請 A(開通 egress)或 B(內部鏡像,我方偏好);誠實揭露本 CVE 對我方幾乎確定不適用但仍不主張豁免;順帶通報 `apt-get update` 失敗仍 exit 0 的陷阱供平台其他服務參考 |
