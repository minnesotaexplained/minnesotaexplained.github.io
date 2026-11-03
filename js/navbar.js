// Shared site navbar behavior (see css/navbar.css for the markup).
// Clicking .mx-nav-toggle flips <html data-theme> between "dark" and "light"
// and saves the choice. Pages with their own theme logic add data-custom to
// the button and handle the click themselves.
document.addEventListener('click', (e) => {
    const btn = e.target.closest('.mx-nav-toggle');
    if (!btn || btn.hasAttribute('data-custom')) return;
    const root = document.documentElement;
    const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
});
