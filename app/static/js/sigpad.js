// Signature pad: draw with mouse, pen or finger. Used where someone signs on a screen.
// Markup: canvas#sig-pad (width/height = 2x logical size), button#sig-clear,
//         form#sig-form with hidden input#signature_data.
(function () {
  var canvas = document.getElementById("sig-pad");
  if (!canvas) { return; }
  var ctx = canvas.getContext("2d");
  var ratio = canvas.width / 640;               // canvas is drawn at 2x for sharpness
  var logicalW = canvas.width / ratio, logicalH = canvas.height / ratio;
  ctx.scale(ratio, ratio);
  ctx.lineWidth = 2.6; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#111111";
  var drawing = false, strokes = 0, last = null;

  function point(e) {
    var r = canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) * (logicalW / r.width), y: (e.clientY - r.top) * (logicalH / r.height) };
  }
  canvas.addEventListener("pointerdown", function (e) {
    e.preventDefault(); drawing = true; last = point(e);
    ctx.beginPath(); ctx.moveTo(last.x, last.y);
    if (canvas.setPointerCapture) { canvas.setPointerCapture(e.pointerId); }
  });
  canvas.addEventListener("pointermove", function (e) {
    if (!drawing) { return; }
    e.preventDefault();
    var p = point(e);
    ctx.lineTo(p.x, p.y); ctx.stroke();
    strokes += Math.abs(p.x - last.x) + Math.abs(p.y - last.y);   // a tap without moving is not a signature
    last = p;
  });
  function stop() { drawing = false; }
  canvas.addEventListener("pointerup", stop);
  canvas.addEventListener("pointercancel", stop);

  document.getElementById("sig-clear").addEventListener("click", function () {
    ctx.clearRect(0, 0, logicalW, logicalH); strokes = 0;
  });
  document.getElementById("sig-form").addEventListener("submit", function (e) {
    if (strokes < 40) {
      e.preventDefault();
      alert("Please sign inside the box first.");
      return;
    }
    document.getElementById("signature_data").value = canvas.toDataURL("image/png");
  });
})();
