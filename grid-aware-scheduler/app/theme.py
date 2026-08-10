"""Shared persistent light/dark appearance control for every product page."""

THEME_BOOTSTRAP = r"""<script>
(function(){try{var saved=localStorage.getItem("grid-aware-theme");
var theme=saved==="light"||saved==="dark"?saved:
(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");
document.documentElement.dataset.theme=theme;}catch(error){}})();
</script>"""

THEME_CSS = r"""
.theme-control{position:fixed;top:14px;right:18px;z-index:10000;display:flex;
align-items:center;gap:7px;padding:7px 9px;border:1px solid var(--sep);
border-radius:999px;background:color-mix(in srgb,var(--card) 88%,transparent);
box-shadow:var(--shadow);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}
.theme-control>span{font-size:10px;font-weight:650;color:var(--text-2);letter-spacing:.02em}
.theme-toggle{position:relative;width:38px;height:22px;display:inline-block;flex:none}
.theme-toggle input{position:absolute;opacity:0;width:1px;height:1px}
.theme-track{position:absolute;inset:0;border-radius:999px;background:var(--text-3);
cursor:pointer;transition:background .18s ease}
.theme-track:after{content:"";position:absolute;width:18px;height:18px;left:2px;top:2px;
border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.32);transition:transform .18s ease}
.theme-toggle input:checked+.theme-track{background:var(--blue,var(--price))}
.theme-toggle input:checked+.theme-track:after{transform:translateX(16px)}
.theme-toggle input:focus-visible+.theme-track{outline:3px solid color-mix(in srgb,var(--blue,var(--price)) 35%,transparent);outline-offset:2px}
@media(max-width:620px){.theme-control{position:absolute;top:10px;right:10px}.theme-control>span{display:none}}
"""

THEME_CONTROL = r"""<div class="theme-control" aria-label="Appearance">
<span>Light</span><label class="theme-toggle">
<input id="themeToggle" type="checkbox" role="switch" aria-label="Use dark mode">
<span class="theme-track" aria-hidden="true"></span></label><span>Dark</span></div>
<script>
(function(){var toggle=document.getElementById("themeToggle");if(!toggle)return;
function apply(theme,persist){document.documentElement.dataset.theme=theme;
toggle.checked=theme==="dark";toggle.setAttribute("aria-checked",String(toggle.checked));
if(persist){try{localStorage.setItem("grid-aware-theme",theme)}catch(error){}}}
apply(document.documentElement.dataset.theme||"light",false);
toggle.addEventListener("change",function(){apply(toggle.checked?"dark":"light",true)});
window.addEventListener("storage",function(event){if(event.key==="grid-aware-theme"&&
(event.newValue==="light"||event.newValue==="dark"))apply(event.newValue,false)});})();
</script>"""
