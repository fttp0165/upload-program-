/* 待開通頁的「複製識別碼」(T56,依 UI 設計稿)。
 *
 * 🔴 本檔只讀頁面上已顯示的 sub 並寫入剪貼簿,不碰任何 token、
 *    也不出現那兩個瀏覽器儲存 API 的名稱——test_sso_contract.py 以字串掃描把關。
 * 按鈕預設 hidden:沒有 JS(或剪貼簿 API 不可用)時使用者仍可反白複製,
 * 功能是漸進增強,不是依賴。
 */
(function () {
  "use strict";
  var btn = document.getElementById("copy-sub");
  var sub = document.getElementById("my-sub");
  var done = document.getElementById("copy-done");
  if (!btn || !sub || !navigator.clipboard) return;

  btn.hidden = false;
  btn.addEventListener("click", function () {
    navigator.clipboard.writeText(sub.textContent.trim()).then(function () {
      if (done) done.hidden = false;
      btn.textContent = "已複製 ✓";
    });
  });
})();
