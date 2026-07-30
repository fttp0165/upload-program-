# CI 改跑 VM self-hosted runner(額度事件的解法)

**建立日期:** 2026-07-31 10:00
**最後更新:** 2026-07-31 10:00
**版本:** v1.0
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
