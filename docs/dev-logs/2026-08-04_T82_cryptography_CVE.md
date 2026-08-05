# T82 修 CI:cryptography CVE-2026-69247(HIGH)擋下建置

**建立日期:** 2026-08-04 11:10
**最後更新:** 2026-08-04 11:30
**版本:** v1.1
**對應任務:** T82

---

## 計畫(動工前成文,憲法第二條 2)

### 起因

PR #24 的 CI 紅了,但**不是 T81 造成的**:

- ✅ 測試 / lint / 文件同步 — 綠(477 passed)
- ❌ Build image + Trivy 掃描 — 紅

Trivy 報告:

```
cryptography (METADATA)  CVE-2026-69247  HIGH  fixed  49.0.0 → 50.0.0
```

`cryptography` 不在 `requirements.txt` 裡,它是 `pyjwt[crypto]` 的**遞移相依**。
requirements 用相容區間而非 lock 檔(檔頭自己註明「正式發版前應改為 lock 檔」),
所以每次建置都抓當下最新的可解版本——這次抓到帶洞的 49.0.0。

**這條紅燈與本分支無關,main 現在建置也會紅**(同一份相依、同一份 CVE 資料庫)。

### 目標

把 CI 建置修回綠燈,並讓這個洞不會再被解回來。

### 為什麼是加下限而不是 lock 檔

改 lock 檔(pip-compile)是對的方向,檔頭也早就這樣寫,但那是**另一件事**:
它會一次釘住全部相依的版本,影響面遠大於一個 CVE,該有自己的計畫與演練。
本次只做最小修正——加一條明確下限,把「不准再解回帶洞版本」寫進 requirements。
lock 檔的事維持原狀,不因為一次 CI 紅燈就順手做掉。

🔴 **不用「暫時忽略這個 CVE」的做法**(`.trivyignore` / `--severity` 放寬)。
這個服務散布可執行檔,掃描是自我加嚴的一環;把掃描調鬆來換綠燈是拿紅線換方便。

### 影響範圍

`requirements.txt` 一行。**對現有資料的影響:🟢 不動資料。**

### 驗收標準

1. `requirements.txt` 明確要求 `cryptography>=50.0.0`;
2. 本機安裝新版後全站測試仍全綠(JWT 驗簽走 pyjwt→cryptography,是實際會用到的路徑);
3. CI 的 Trivy 步驟轉綠。

### 回滾方式

純相依版本、無 migration:改回原本的行不指定即可(但那等於把洞放回來)。

---

## 結果

### 做了什麼

`requirements.txt` 新增一行明確下限,並在旁註明原因與「可在下次 lock 檔化時重新檢視」。

### 驗證

- 本機升級到 `cryptography 50.0.0` 後跑全站:**477 passed**(與升級前同數,無行為差異)。
  JWT 驗簽(RS256/JWKS)是 `pyjwt[crypto]` → `cryptography` 的實際使用路徑,
  `test_token_verify.py` 與 `test_sso_contract.py` 都會走到,不是只裝了沒用。
- `ruff check` 乾淨。
- CI 的 Trivy 步驟需由 CI 實跑確認(本機無 Docker,不宣稱已驗)。

### 對現有資料的實際影響

🟢 **無**。

### 遺留問題與後續建議

1. **requirements 仍是相容區間,不是 lock 檔。** 這代表「建置不可重現」——今天抓到
   CVE 的是 `cryptography`,下次可能是任何一個遞移相依,而且**同一個 tag 重建可能
   得到不同內容**。檔頭的 TODO(pip-compile)建議另立任務處理,不要一直靠 CI 紅燈提醒。
2. main 也帶著同一個洞,直到本 PR 併入為止。

---

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-08-04 11:10 | Claude(CI 紅燈觸發) | 動工前計畫:根因(遞移相依 + 相容區間)、為什麼不改 lock 檔、為什麼不 ignore CVE |
| v1.1 | 2026-08-04 11:30 | Claude | 補上結果:升級後全站 477 綠;遺留仍是 lock 檔化 |
