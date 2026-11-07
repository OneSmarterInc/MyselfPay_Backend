document.addEventListener('DOMContentLoaded', () => {
  const pageCheckbox = document.getElementById('mailer-select-page');
  const matchingCheckbox = document.getElementById('mailer-select-filtered');
  const form = document.getElementById('mailer-compose');
  const boxes = [...document.querySelectorAll('.mailer-center-checkbox:not(:disabled)')];
  const status = document.getElementById('mailer-selection-status');
  const range = document.getElementById('id_selection');
  const matchingCount = Number(form?.dataset.matchingCount || 0);
  if (pageCheckbox) pageCheckbox.disabled = boxes.length === 0;
  function update() {
    const allMatching = !!matchingCheckbox?.checked;
    let count = allMatching ? matchingCount : boxes.filter(box => box.checked).length;
    if (pageCheckbox) { pageCheckbox.checked = boxes.length > 0 && boxes.every(box => box.checked); pageCheckbox.indeterminate = !allMatching && count > 0 && count < boxes.length; }
    if (!allMatching && !count && range?.value.trim()) {
      const match = range.value.trim().match(/^([1-9]\d*)(?:\s*-\s*([1-9]\d*))?$/);
      if (match) { const start = match[2] ? Number(match[1]) : 1; const end = Number(match[2] || match[1]); if (end >= start && end <= matchingCount) count = end - start + 1; }
    }
    const text = `${count} centers selected${allMatching ? ' across all matching results' : ''}`;
    document.querySelectorAll('.mailer-selected-count').forEach(el => { el.textContent = text; });
    if (status) status.textContent = allMatching ? `${text}. The preview will include every matching page.` : `${text}. Select all matching centers to include every page, or choose individual rows/count/range.`;
    document.querySelectorAll('.mailer-pagination a').forEach(link => { const url = new URL(link.getAttribute('href'), location.href); if (allMatching) url.searchParams.set('all_matching', '1'); else url.searchParams.delete('all_matching'); link.setAttribute('href', url.pathname + url.search); });
  }
  matchingCheckbox?.addEventListener('change', () => { boxes.forEach(box => { box.checked = matchingCheckbox.checked; }); update(); });
  pageCheckbox?.addEventListener('change', () => { if (matchingCheckbox) matchingCheckbox.checked = false; boxes.forEach(box => { box.checked = pageCheckbox.checked; }); update(); });
  boxes.forEach(box => box.addEventListener('change', () => { if (matchingCheckbox) matchingCheckbox.checked = false; update(); }));
  range?.addEventListener('input', update);
  if (matchingCheckbox?.checked) boxes.forEach(box => { box.checked = true; });
  update();
});
