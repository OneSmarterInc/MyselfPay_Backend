document.addEventListener('DOMContentLoaded', () => {
  const all = document.getElementById('mailer-select-page');
  const boxes = [...document.querySelectorAll('.mailer-center-checkbox:not(:disabled)')];
  const status = document.getElementById('mailer-selection-status');
  if (all) all.disabled = boxes.length === 0;
  function update() {
    const count = boxes.filter(box => box.checked).length;
    if (all) { all.checked = boxes.length > 0 && count === boxes.length; all.indeterminate = count > 0 && count < boxes.length; }
    if (status) status.textContent = `${count} centers selected on this page. Checked centers override the count/range.`;
  }
  all?.addEventListener('change', () => { boxes.forEach(box => { box.checked = all.checked; }); update(); });
  boxes.forEach(box => box.addEventListener('change', update));
  update();
});
