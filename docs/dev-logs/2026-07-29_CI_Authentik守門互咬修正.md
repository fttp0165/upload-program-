# 🐛 CI 修正:Authentik 紅線 grep 抓到自己的守門測試

**建立日期:** 2026-07-29 06:50
**最後更新:** 2026-07-29 06:50
**版本:** v1.0
**對應任務:** 缺陷修正(源頭為 T51,於 PR #3 的 CI 上現形)

> **本篇為缺陷修正日誌**:發現即修,計畫與修正同步成文。

## 現象

PR #3 的 CI「測試 / lint / 文件同步」紅燈,失敗在「檢查 SSO 紅線」步驟:

```
tests/test_sso_contract.py:182:def test_repo無Authentik與HS256():
##[error]出現 Authentik —— 身分一律走 Keycloak
```

## 根本原因

**不是 PR #3 引入的。** CI 的紅線步驟 `grep -rniE 'authentik' ... app/ tests/` 與
T51(commit `1418744`,已隨 PR #2 合入 main)新增的守門測試互咬:
`test_repo無Authentik與HS256` **必然含有它負責掃描的那個字**。
所以從 T51 那個 commit 起,CI 就被自己的守門員絆倒——main 上同樣是紅的。

這是「字串掃描器抓到自己的守門員」家族的**第四例**
(前三例:CSS 註釋裡的 `prefers-color-scheme`、模板註釋裡的 `|safe`、
JS 註釋裡的 `localStorage`)。pytest 那側早就預見這件事——它刻意只掃
`app/` 不掃 `tests/`,理由相同;**CI 的 grep 沒有做同樣的排除**,T51 當時漏了。

為什麼拖到現在才現形:T51 之後的三次 push(PR #2 的尾段)CI 結果沒有被逐一
盯到紅燈步驟,PR #2 合併時亦未擋。教訓:**紅燈不分「哪個 job」,合併前要看全部**。

## 修正

`ci.yml` 的 grep 加 `--exclude='test_sso_contract.py'`——**具名例外,不是放寬**:
只排除「職責就是寫出禁字」的那一個檔案,`tests/` 其餘檔案仍在掃描範圍;
`app/` 完全不變,且該檔對 `app/` 的 pytest 掃描照常執行(雙保險仍在)。

排除的理由寫成 ci.yml 內的註釋(第五條 4:修 bug 註明根本原因)。

## 測試結果

- 本機重演 CI 兩個檢查(含修正後排除):`PASS authentik`、`PASS hs256`
- 守門測試本身仍綠:`test_sso_contract.py` 11 passed(它掃 `app/`,不受影響)
- 反向驗證非空轉:對 `app/` 任一檔案臨時加入 `authentik` 字樣,grep 仍會紅
  (排除只作用於那一個測試檔)

## 對現有資料的影響

🟢 純 CI 設定。

## 版本歷史

| 版本 | 日期 | 修改人 | 摘要 |
|---|---|---|---|
| v1.0 | 2026-07-29 06:50 | Claude(Benny 授權) | 初版:根因(T51 守門測試與 CI grep 互咬,第四例)、具名例外修法、本機重演證據 |
