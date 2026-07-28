/* 上傳檔案:XHR PUT + 進度條(T44 / F74)。
 *
 * 🔴 為什麼是 XHR 而不是 <form>:HTML 表單只能發 GET/POST,且只能送 urlencoded
 *    或 multipart,**發不出 raw body 的 PUT**。而 API 刻意用 raw body PUT——
 *    multipart 解析會把大檔落到暫存檔,違反「容器內不寫檔當狀態」。
 *
 * 🔴 為什麼是外部檔案:CSP 是 `default-src 'self'`,inline script 一律被擋。
 *
 * 🔴 **本檔不碰任何 token**。上傳靠 HttpOnly session cookie,瀏覽器自動帶上;
 *    JS 讀不到也不需要讀。契約 §4.10:同源之下瀏覽器的儲存空間全平台可讀,
 *    token 放進去等於公開。
 *    註:本檔刻意不出現那兩個儲存 API 的名稱——test_sso_contract.py 以字串掃描把關,
 *    而對 JS 來說「只掃字串」比「解析註解」可靠得多,所以遷就的是註解不是測試。
 *
 * 🔴 端點與上限由伺服器放在 data-* 屬性裡。JS **不自己拼路徑**:
 *    它不知道部署的路徑前綴是什麼,猜錯就是 404(PLM 出過那個事故)。
 */
(function () {
  "use strict";

  var root = document.getElementById("uploader");
  if (!root) return;

  var base = root.dataset.uploadBase;
  var maxBytes = parseInt(root.dataset.maxBytes, 10);

  var fileInput = document.getElementById("file-input");
  var kindInput = document.getElementById("kind-input");
  var button = document.getElementById("upload-button");
  var bar = document.getElementById("upload-progress");
  var status = document.getElementById("upload-status");

  function say(message) {
    status.textContent = message;
  }

  function humanBytes(n) {
    if (n < 1024) return n + " bytes";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  button.addEventListener("click", function () {
    var file = fileInput.files && fileInput.files[0];
    if (!file) {
      say("請先選擇檔案。");
      return;
    }
    if (file.size > maxBytes) {
      // 先在瀏覽器擋一次,省得傳完 100 MB 才被伺服器退回。
      // 伺服器端仍會再擋一次——前端的檢查永遠只是體驗,不是防線。
      say("檔案太大:" + humanBytes(file.size) + ",單檔上限 " + humanBytes(maxBytes) + "。");
      return;
    }

    // 檔名要編碼才能安全地放進網址(中文檔名、空白、# 都會出事)。
    var url = base + "/" + encodeURIComponent(file.name) +
              "?kind=" + encodeURIComponent(kindInput.value);

    var xhr = new XMLHttpRequest();
    xhr.open("PUT", url, true);
    // 讓伺服器的錯誤回應走 problem+json 而不是 HTML(T47 的協商規則)。
    xhr.setRequestHeader("Accept", "application/json");

    bar.hidden = false;
    bar.value = 0;
    button.disabled = true;

    xhr.upload.onprogress = function (event) {
      if (!event.lengthComputable) return;
      var percent = Math.round((event.loaded / event.total) * 100);
      bar.value = percent;
      say("上傳中 " + percent + "%(" + humanBytes(event.loaded) + " / " + humanBytes(event.total) + ")");
    };

    xhr.onload = function () {
      button.disabled = false;
      if (xhr.status === 201) {
        say("上傳完成,重新整理頁面…");
        window.location.reload();
        return;
      }
      // 失敗提示:盡量把伺服器說的原因顯示出來,而不是一句「失敗」。
      var detail = "";
      try {
        detail = JSON.parse(xhr.responseText).detail || "";
      } catch (e) {
        detail = "";
      }
      bar.hidden = true;
      say("上傳失敗(" + xhr.status + ")" + (detail ? ":" + detail : "。請稍後再試。"));
    };

    xhr.onerror = function () {
      button.disabled = false;
      bar.hidden = true;
      say("上傳失敗:連線中斷。請檢查網路後再試一次。");
    };

    xhr.send(file);
  });
})();
