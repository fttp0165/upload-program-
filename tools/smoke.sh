#!/usr/bin/env bash
# upload-program 換版冒煙腳本(T91)。
#
# 把 runbook §A.4 的四組檢查中「可腳本化」的部分做成一鍵:逐項印 ✅/❌/⏭,
# 有任何 ❌ 就以非 0 退出。人工逐條貼指令仍是後備路徑(runbook 保留原指令)。
#
# 🔴 界線:登入後的項目(下載標頭、管理頁、上傳三格)**刻意不自動化**——
#    契約只允許 Authorization Code + PKCE,把帳密塞給腳本走 ROPC 是禁手,
#    而且等於在 VM 上多放一份會外洩的憑證。那些項目印 ⏭ SKIP 指回
#    《測試項目清單》,由人工執行;**跳過要看得見,不得假裝驗過**。
#
# 用法(VM,隨 compose 一起 scp,同 backup.sh):
#   ./smoke.sh --vm --expect v0.2.1
# 本機開發驗證:
#   ./smoke.sh --base http://127.0.0.1:8123 --local --expect v0.2.1
set -u

BASE="https://catsapp.sporton.com.tw/upload"
EXPECT=""
LOCAL=0   # 本機模式:無 gateway(不驗既有系統;X-Frame-Options 預期 0 個)
VM=0      # VM 模式:加驗容器內 /ready(要在 compose 目錄執行)

while [ $# -gt 0 ]; do
  case "$1" in
    --base)   BASE="$2"; shift 2 ;;
    --expect) EXPECT="$2"; shift 2 ;;
    --local)  LOCAL=1; shift ;;
    --vm)     VM=1; shift ;;
    *) echo "未知參數:$1(可用 --base URL / --expect vX.Y.Z / --local / --vm)"; exit 2 ;;
  esac
done
BASE="${BASE%/}"
# 既有系統檢查要打「站台根」而不是本服務前綴——從 BASE 取 scheme://host。
ROOT="$(printf '%s' "$BASE" | sed -E 's|^(https?://[^/]+).*$|\1|')"

PASS=0; FAIL=0; SKIP=0
ok()   { printf '✅ %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '❌ %s\n' "$1"; FAIL=$((FAIL+1)); }
note() { printf '⏭  SKIP %s\n' "$1"; SKIP=$((SKIP+1)); }

# 統一走 -sk:TLS 由 gateway 終結,內網憑證鏈在部分機器不完整(runbook 慣例)。
http_code() { curl -sk -o /dev/null -w '%{http_code}' -H "Accept: $2" "$1"; }

echo "== upload-program 冒煙:$BASE =="

# --- [J-01][A-12] 對外路徑(機器視角必須 200——監控判準,不隨登入改動而變)---
c="$(http_code "$BASE/" '*/*')"
[ "$c" = "200" ] && ok "[J-01/A-12] 首頁(機器視角)= 200" || bad "[J-01/A-12] 首頁(機器視角)= $c(預期 200)"

c="$(http_code "$BASE/static/app.css" '*/*')"
[ "$c" = "200" ] && ok "[J-01] 靜態檔 /static/app.css = 200" || bad "[J-01] 靜態檔 = $c(預期 200)"

# --- [K-06][J-02] 版本哨兵:/help 頁尾版本號(匿名可驗,登入壞掉也能驗)---
page="$(curl -sk -H 'Accept: text/html' "$BASE/help")"
# 🐛 本機實跑抓到:/help 教學內文有範例版本號(v1.0.0),裸抓第一個 vX.Y.Z 會撿錯。
# 錨定 T68 的頁尾固定格式「upload-program vX.Y.Z」——那才是哨兵,不是內文裡的舉例。
detected="$(printf '%s' "$page" | grep -oE 'upload-program v[0-9]+\.[0-9]+\.[0-9]+' | head -1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' || true)"
if [ -n "$EXPECT" ]; then
  if [ "$detected" = "$EXPECT" ]; then
    ok "[K-06/J-02] 版本哨兵 = $detected"
  else
    bad "[K-06/J-02] 版本哨兵 = ${detected:-偵測不到}(預期 $EXPECT)——可能換版沒生效(2026-08-11 假換版就是這樣抓到的)"
  fi
else
  ok "[K-06/J-02] 版本哨兵偵測到 ${detected:-無}(未給 --expect,僅回報不判定)"
fi

# --- [A-01] 匿名瀏覽器開首頁 → 302 到登入(T81)---
redirect="$(curl -sk -o /dev/null -w '%{http_code} %{redirect_url}' -H 'Accept: text/html' "$BASE/")"
case "$redirect" in
  302*auth/login*) ok "[A-01] 匿名瀏覽器首頁 302 → 登入" ;;
  *)               bad "[A-01] 匿名瀏覽器首頁:$redirect(預期 302 → …/auth/login)" ;;
esac

# --- [I-01][I-02] 安全標頭(以 /help 為載體)---
headers="$(curl -skI -H 'Accept: text/html' "$BASE/help")"
printf '%s' "$headers" | grep -qi 'x-content-type-options: *nosniff' \
  && ok "[I-02] nosniff 存在" || bad "[I-02] 缺 X-Content-Type-Options: nosniff"
printf '%s' "$headers" | grep -qi "default-src 'self'" \
  && ok "[I-01] CSP 含 default-src 'self'" || bad "[I-01] CSP 缺失或被改"
xfo="$(printf '%s' "$headers" | grep -ci '^x-frame-options' || true)"
if [ "$LOCAL" = "1" ]; then
  # 本機沒有 gateway:App 依施工單不得自送,預期 0 個。
  [ "$xfo" = "0" ] && ok "[I-02] X-Frame-Options 0 個(本機,App 不自送)" \
    || bad "[I-02] X-Frame-Options 出現 $xfo 個(本機預期 0——App 不得自送)"
else
  # 經 gateway:恰好 1 個(gateway 送);2 個 = App 也送了,行為未定義(施工單 §5.2)。
  [ "$xfo" = "1" ] && ok "[I-02] X-Frame-Options 恰 1 個(gateway)" \
    || bad "[I-02] X-Frame-Options $xfo 個(預期恰 1;2 個=App 重複送,0 個=gateway 沒送)"
fi

# --- [J-01] 既有系統零影響(只在經 gateway 時有意義)---
if [ "$LOCAL" = "1" ]; then
  note "[J-01] 既有系統零影響——本機模式無 gateway,換版時務必在 VM 跑"
else
  for p in / /plm/ /TMP_GEN/ /core/health; do
    c="$(http_code "$ROOT$p" '*/*')"
    [ "$c" = "200" ] && ok "[J-01] 既有系統 $p = 200" || bad "[J-01] 既有系統 $p = $c(預期 200)——動到別人了,先回滾再查"
  done
fi

# --- [J-01] 容器內 readiness(DB / MinIO / JWKS;容器重建過就要重驗)---
if [ "$VM" = "1" ]; then
  r="$(docker compose exec -T svc python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/ready').status)" 2>/dev/null || echo ERR)"
  [ "$r" = "200" ] && ok "[J-01] /ready = 200" || bad "[J-01] /ready = $r(預期 200)"
else
  note "[J-01] /ready——需在 VM 的 compose 目錄以 --vm 執行"
fi

# --- 腳本構不到的,明示跳過(不得假裝驗過)---
note "[F-01/E-02/G-01/H-*] 需登入的項目(下載標頭、三類上傳、回報、管理頁)→ 人工,見《測試項目清單》"
note "[A-10/A-11] 跨 App 單一登出 → 人工"

echo "== 結果:✅ $PASS ・ ❌ $FAIL ・ ⏭ $SKIP =="
[ "$FAIL" = "0" ] || { echo "🔴 有 ❌:換版不算完成(runbook §A.4:四組全對才算)"; exit 1; }
