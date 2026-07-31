/* T78:回報詳情頁的「貼上 / 拖曳截圖」漸進增強。
 *
 * 🔴 這支 JS 只是**便利**:同一頁上的 <input type="file"> 表單是下層,
 *    JS 壞掉、被擋、或使用者關掉,附圖仍然可用(有測試釘住那條路徑)。
 * 🔴 不碰 token:上傳靠 HttpOnly session cookie(契約 §4.10——token 進不了 JS)。
 * 🔴 端點路徑由伺服器算好放在 data-upload-base:JS 不知道前綴是什麼,也不該自己拼。
 */
(function () {
  "use strict";

  var box = document.getElementById("attach");
  if (!box) return;
  var base = box.getAttribute("data-upload-base");
  var hint = document.getElementById("paste-hint");
  var textareas = document.querySelectorAll("textarea[name='body_markdown']");
  if (!base || !textareas.length) return;

  function say(message) {
    if (hint) hint.textContent = message;
  }

  function upload(file, textarea) {
    var name = file.name || "screenshot.png";
    say("上傳中:" + name);

    var xhr = new XMLHttpRequest();
    xhr.open("PUT", base + "/" + encodeURIComponent(name), true);
    xhr.onload = function () {
      if (xhr.status === 201) {
        var data = JSON.parse(xhr.responseText);
        // 把 markdown 插進輸入框——使用者不必自己拼路徑,也就不會拼錯。
        textarea.value = textarea.value + (textarea.value ? "\n\n" : "") + data.markdown;
        say("已附上 " + name + ",送出後就會顯示。");
      } else if (xhr.status === 413) {
        say("圖片太大(單張上限 5 MB)。");
      } else if (xhr.status === 422) {
        say("這個檔案不是 PNG / JPEG / GIF,或已達 5 張上限。");
      } else {
        say("上傳失敗(" + xhr.status + "),可以改用下面的表單。");
      }
    };
    xhr.onerror = function () {
      say("上傳失敗,可以改用下面的表單。");
    };
    xhr.send(file);
  }

  Array.prototype.forEach.call(textareas, function (textarea) {
    textarea.addEventListener("paste", function (event) {
      var items = (event.clipboardData || {}).items || [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].type && items[i].type.indexOf("image/") === 0) {
          var file = items[i].getAsFile();
          if (file) {
            event.preventDefault();
            upload(file, textarea);
          }
          return;
        }
      }
    });

    textarea.addEventListener("dragover", function (event) {
      event.preventDefault();
    });
    textarea.addEventListener("drop", function (event) {
      var files = (event.dataTransfer || {}).files || [];
      if (files.length && files[0].type.indexOf("image/") === 0) {
        event.preventDefault();
        upload(files[0], textarea);
      }
    });
  });
})();
