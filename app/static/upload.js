/* 上傳檔案:XHR PUT + 進度條(T44 / F74;T86 改為三格卡片 + 可取消)。
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
 *
 * 🐛 T86 修掉「無法取消」:原本送出後 `button.disabled = true`,而畫面上沒有
 *    第二個可按的東西;XHR 又**沒有設 timeout**,所以連線卡住(不是斷線——
 *    斷線才會觸發 onerror)時進度條會停在某個百分比、按鈕永遠停用,
 *    使用者只能重新整理整頁。修法是兩件事一起做:取消鈕 + 逾時。
 */
(function () {
  "use strict";

  var root = document.getElementById("uploader");
  if (!root) return;

  var base = root.dataset.uploadBase;
  var maxBytes = parseInt(root.dataset.maxBytes, 10);
  // 逾時放寬到 10 分鐘:單檔上限 100 MB,慢一點的內網也要傳得完。
  // 這個值不是效能調校,是「卡住的連線最晚多久會放人走」。
  var TIMEOUT_MS = 10 * 60 * 1000;

  function humanBytes(n) {
    if (n < 1024) return n + " bytes";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  // 一張卡片 = 一個獨立的上傳器。彼此不共用狀態,所以同時傳兩格也不會互相蓋掉。
  Array.prototype.forEach.call(root.querySelectorAll(".upload-card"), function (card) {
    var kind = card.dataset.kind;
    var fileInput = card.querySelector(".upload-file");
    var startButton = card.querySelector(".upload-start");
    var cancelButton = card.querySelector(".upload-cancel");
    var bar = card.querySelector(".upload-progress");
    var status = card.querySelector(".upload-status");
    var inFlight = null;

    function say(message) {
      status.textContent = message;
    }

    function idle() {
      inFlight = null;
      startButton.disabled = false;
      cancelButton.hidden = true;
      bar.hidden = true;
    }

    startButton.addEventListener("click", function () {
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
      // kind 來自這張卡片,不再由使用者從下拉選——選錯的機會少一個。
      var url = base + "/" + encodeURIComponent(file.name) +
                "?kind=" + encodeURIComponent(kind);

      var xhr = new XMLHttpRequest();
      inFlight = xhr;
      xhr.open("PUT", url, true);
      // 讓伺服器的錯誤回應走 problem+json 而不是 HTML(T47 的協商規則)。
      xhr.setRequestHeader("Accept", "application/json");
      xhr.timeout = TIMEOUT_MS;

      bar.hidden = false;
      bar.value = 0;
      startButton.disabled = true;
      cancelButton.hidden = false;

      xhr.upload.onprogress = function (event) {
        if (!event.lengthComputable) return;
        var percent = Math.round((event.loaded / event.total) * 100);
        bar.value = percent;
        say("上傳中 " + percent + "%(" + humanBytes(event.loaded) + " / " + humanBytes(event.total) + ")");
      };

      xhr.onload = function () {
        idle();
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
        say("上傳失敗(" + xhr.status + ")" + (detail ? ":" + detail : "。請稍後再試。"));
      };

      xhr.onerror = function () {
        idle();
        say("上傳失敗:連線中斷。請檢查網路後再試一次。");
      };

      xhr.ontimeout = function () {
        idle();
        say("上傳逾時(超過 " + (TIMEOUT_MS / 60000) + " 分鐘沒有回應),請再試一次。");
      };

      // abort() 會觸發 onabort 而不是 onerror——沒有這一段,取消之後畫面會停在
      // 「上傳中 47%」不動,看起來像當掉。
      xhr.onabort = function () {
        idle();
        say("已取消。");
      };

      xhr.send(file);
    });

    cancelButton.addEventListener("click", function () {
      if (inFlight) inFlight.abort();
    });
  });
})();
