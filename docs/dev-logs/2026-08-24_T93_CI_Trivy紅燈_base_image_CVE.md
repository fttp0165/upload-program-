# T93:CI Trivy 紅燈——base image 的 util-linux CVE

**建立日期:** 2026-08-24 14:35
**最後更新:** 2026-08-24 15:05
**版本:** v1.1
**任務編號:** T93

---

## 計畫(動工前寫,第二條 2)

### 現象

PR #28 的 CI:**`測試 / lint / 文件同步` 綠(527 passed)**,
**`Build image + Trivy 掃描` 紅**,exit code 1。

```
util-linux / mount / login / bsdutils …
CVE-2026-53612  TOCTOU in the mount program (post-mount ownership/mode changes)
CVE-2026-53613  TOCTOU via ancestor directory swap
CVE-2026-53614  SUID mount(8) allows nosuid/noexec bypass via LIBMOUNT_FORCE_MOUNT2
CVE-2026-53615  Integer overflow in libblkid/src/partitions/dos.c
```

### 根因(與本批改動無關)

- 這些是 **base image `python:3.12-slim` 的 OS 套件**,不是 Python 相依;
  PR #28 沒有動 `Dockerfile` 也沒有動 `requirements*.txt`。
- **上一次 main 的 CI 是綠的**(2026-08-11,run #173)——期間 Debian 公布了這四支,
  所以是**環境漂移**,與 T82 同型(當時是 `cryptography` 的遞移相依)。
- Trivy 帶 `--ignore-unfixed`,**能被列出就代表 Debian 已有修版**。

### 🔴 兩條看似顯然、但在本專案行不通的修法

| 修法 | 為什麼不行 |
|---|---|
| Dockerfile 加 `apt-get upgrade` | **VM 的網路連不到 apt 套件庫**——這正是 2026-07-30 run #85 把 builder 的 `apt-get` 整段拿掉的原因(Dockerfile 檔頭有紀錄)。加回去等於讓建置在同一個地方再炸一次 |
| `.trivyignore` 放行 | T82 已明文拒絕過:「本服務散布可執行檔,掃描是自我加嚴的一環,調鬆掃描是拿紅線換方便」 |

### 做法(先試最便宜、風險最低的那一步)

CI 的 build 步驟改成 `docker build --pull`:

- base image 走 **dockerd 的 registry mirror**(`mirror.gcr.io`)拉,那條路是通的
  (現行 CI 就是靠它抓 base image)。
- 現行 `docker build` **沒有** `--pull`,而 runner 是**同一台 VM**、
  本機層快取長期存在 → **它很可能一直在用一份舊的 `python:3.12-slim`**。
  上游若已重建含修好的 util-linux,`--pull` 就直接解決。
- 這一步**不改 Dockerfile、不改相依、不動掃描門檻**,失敗也只是回到現狀。

⚠ **若 `--pull` 之後仍紅**(代表上游還沒重建),選項依序:
1. 等上游重建(Debian 修版通常幾天內進官方 image);此期間**不發版**。
2. 改 base image 到已含修版的 tag(需重跑全套 + 冒煙,另開任務)。
3. 由 Benny 明示、逐 CVE、帶到期日的一次性例外(**我不主動做**,且不用 `.trivyignore` 通吃)。

### 影響範圍

- `.github/workflows/ci.yml`(build 步驟加 `--pull`,並註記理由)

**對現有資料的影響:🟢 不動。** 不碰程式、不碰相依、不碰掃描門檻。

### 驗收標準

1. CI 的 `Build image + Trivy 掃描` 轉綠。
2. 掃描門檻**一字未改**(仍 `--severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed`)。
3. `測試 / lint / 文件同步` 維持綠(527)。

### 🔴 本任務不寫新測試的理由(第三條 4)

CI 的建置環境**無法在本機重現**:這個容器裡沒有 docker daemon
(`docker info` → `no docker daemon`),Trivy 也不在。
**所以驗證只能在 CI 上發生**,而不是我宣稱它會過。
—— 這一條刻意寫進來:本專案第三條 5 說「測試全綠指實際跑過」,
我在這裡能提供的證據只有 CI 的那一格,不是本機的綠燈。

### 回滾方式

`git revert`;`--pull` 移除即回到現行行為。

---

## 結果

### 三輪 CI 換來的三個事實

| 嘗試 | 結果 |
|---|---|
| ① CI build 加 `--pull` | ❌ **仍紅**。拿到的 base image digest 沒變 |
| ② 診斷步驟(base image 資訊 + apt 可達性) | base created **2026-07-14**、digest `sha256:57cd7c3a…`、**`apt` = APT_OK** |
| ③ runtime 階段指定套件升級 | 見下(本次提交) |

```
base image created : 2026-07-14T02:11:29Z
base image digest  : python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
apt 套件庫         : APT_OK
Trivy              : Total 36 (HIGH: 36) —— 全部 src:util-linux,Installed 2.41-5 → Fixed 2.41.5-0+deb13u1
```

### 🔴 計畫錯在哪(第二條 5 回寫)

計畫段把「Dockerfile 加 `apt-get upgrade`」列為**行不通**,理由是 Dockerfile 檔頭
2026-07-30 的紀錄「VM 連不到 apt 套件庫」。

**那則紀錄已經過期** —— 2026-08-24 在 CI 上實測回 `APT_OK`。
所以真正該做的第一件事不是 `--pull`,而是**先驗證那個前提還成不成立**。

⚠ 這件事本身就是教訓:**檔頭那行註解讓一整條正確的修法被我先排除掉了。**
註解記的是「當時的環境」,而環境會變;下次看到「某條路不通」的紀錄,
先問它是哪一天寫的、以及有沒有便宜的方法當場重驗。
Dockerfile 檔頭已加上 2026-08-24 的更正,不讓下一個人再被同一句話擋一次。

### 做法(本次提交)

runtime 階段(`USER app` 之前)升級 Trivy 表列的**八個 util-linux 系列套件**:

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade \
       util-linux mount login bsdutils \
       libblkid1 libmount1 libsmartcols1 libuuid1 liblastlog2-2 \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f='util-linux 升級後版本:${Version}\n' util-linux
```

- **寫死清單而不是 `apt-get upgrade`**:這八個就是 Trivy 表的全部,明列讓「這次修了什麼」
  在 diff 上看得見;`upgrade` 會把不相關的套件一起動,出問題時分不出是誰。
- `--pull` **保留**(build 與 publish 兩個 job 都加):它本身是對的,只是不夠。
- 診斷步驟**已移除**(事實留在本篇與 Dockerfile 註解裡)。
- 🔴 掃描門檻**一字未改**:仍 `--severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed`。

### 對現有資料的實際影響

🟢 **不動。** 不碰程式、不碰 Python 相依、不碰資料。image 會大幾 MB。

### 遺留問題與後續建議

1. **這是打補丁,不是根治。** 上游 `python:3.12-slim` 一旦重建,這一段就變成多餘
   (但無害)。真正的根治是 base image 鎖 digest + 定期升 digest 的例行工作,
   或改用會自動跟上安全更新的建置方式。
2. 下一次「別的來源」的 CVE 還是要再加一行 —— 那時值得考慮改成
   `apt-get upgrade` + 明確記錄理由,而不是清單越長。
3. 🔴 **CI 綠燈才算完成**:這個環境沒有 docker daemon 也沒有 Trivy,
   我在本機無法驗證這段 `RUN`;證據只能是 CI 那一格。
