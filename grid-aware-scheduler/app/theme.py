"""Shared appearance and primary navigation for every product page."""

from __future__ import annotations

import html

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
.menu-button{display:inline-flex;flex-direction:column;justify-content:center;gap:3px;width:26px;height:22px;
padding:0 5px;border:0;border-radius:6px;background:transparent;cursor:pointer}
.menu-button span{display:block;height:2px;border-radius:2px;background:var(--text-2)}
.menu-button:hover{background:color-mix(in srgb,var(--text) 7%,transparent)}
.menu-button:focus-visible{outline:3px solid color-mix(in srgb,var(--blue,var(--price)) 35%,transparent)}
.menu-sep{width:1px;height:16px;background:var(--sep)}
.side-scrim{position:fixed;inset:0;z-index:10001;background:rgba(0,0,0,.28)}
.side-menu{position:fixed;top:0;right:0;bottom:0;z-index:10002;width:min(320px,86vw);padding:18px;
box-sizing:border-box;background:var(--card);border-left:1px solid var(--sep);box-shadow:var(--shadow);
transform:translateX(105%);visibility:hidden;transition:transform .2s ease,visibility .2s}
.side-menu.open{transform:none;visibility:visible}
.side-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;
color:var(--text);font-size:15px}
.side-close{border:0;background:transparent;color:var(--text-2);font-size:24px;line-height:1;cursor:pointer;
padding:2px 8px;border-radius:6px}
.side-close:hover{background:color-mix(in srgb,var(--text) 7%,transparent)}
.side-item{display:block;padding:12px 14px;border:1px solid var(--sep);border-radius:12px;
color:var(--text);text-decoration:none}
.side-item:hover{background:color-mix(in srgb,var(--text) 5%,transparent)}
.side-item b{display:block;font-size:14px}.side-item small{color:var(--text-2);font-size:12px}
.side-note{margin:10px 2px 0;color:var(--text-2);font-size:12px}
.product-nav{display:flex!important;align-items:center;gap:3px!important;max-width:100%;
overflow-x:auto;padding:4px!important;border:1px solid var(--sep)!important;border-radius:12px!important;
background:color-mix(in srgb,var(--card) 76%,transparent)!important;scrollbar-width:none}
.product-nav::-webkit-scrollbar{display:none}.product-nav a{display:inline-flex;align-items:center;
min-height:34px;padding:7px 13px!important;border-radius:8px!important;color:var(--text-2)!important;
font-size:12.5px!important;font-weight:620!important;text-decoration:none!important;white-space:nowrap}
.product-nav a:hover{color:var(--text)!important;background:color-mix(in srgb,var(--text) 5%,transparent)!important}
.product-nav a.on,.product-nav a[aria-current="page"]{color:var(--text)!important;background:var(--card)!important;
box-shadow:0 1px 3px rgba(0,0,0,.10)!important}
.product-nav .nav-divider{width:1px;height:20px;background:var(--sep);margin:0 2px;flex:none}
.product-name{display:block;margin:0 0 5px;color:var(--blue,var(--price));font-size:11px;
font-weight:750;letter-spacing:.1em;text-transform:uppercase}
@media(max-width:620px){.product-nav{width:100%}.product-nav a{padding:7px 11px!important}}
.page-loading{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:10003;padding:9px 14px;
border:1px solid var(--sep);border-radius:10px;background:var(--card);color:var(--text);box-shadow:var(--shadow);
font-size:13px}
"""


_NAVIGATION = (
    ("overview", "Overview", "/"),
    ("plan", "Plan work", "/planner"),
    ("hardware", "Hardware", "/simulator"),
    ("energy", "Energy", "/grid"),
    ("site", "Site setup", "/site"),
    ("history", "History", "/decisions"),
)


def product_nav(active: str, hrefs: dict[str, str] | None = None) -> str:
    """Render one task-labelled navigation contract across every page."""
    hrefs = hrefs or {}
    links = []
    for index, (key, label, default_href) in enumerate(_NAVIGATION):
        if index == 4:
            links.append('<span class="nav-divider" aria-hidden="true"></span>')
        current = ' class="on" aria-current="page"' if key == active else ""
        href = html.escape(hrefs.get(key, default_href), quote=True)
        links.append(f'<a href="{href}"{current}>{label}</a>')
    return '<nav class="product-nav" aria-label="Primary navigation">' + "".join(links) + "</nav>"

THEME_CONTROL = r"""<div class="theme-control" aria-label="Appearance">
<button class="menu-button" id="menuButton" type="button" aria-label="Open menu"
aria-controls="sideMenu" aria-expanded="false"><span></span><span></span><span></span></button>
<span class="menu-sep" aria-hidden="true"></span>
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
</script>
<div class="side-scrim" id="sideScrim" hidden></div>
<aside class="side-menu" id="sideMenu" aria-label="Tools" aria-hidden="true">
<div class="side-head"><strong>Tools</strong>
<button class="side-close" id="sideClose" type="button" aria-label="Close menu">&times;</button></div>
<a class="side-item" href="/national-grid" target="_blank" rel="noopener" id="nationalGridLink">
<b>National Grid Tool</b><small>GB electricity market data, history and models</small></a>
<p class="side-note" id="nationalGridNote" hidden>Starting the National Grid Tool&hellip; it opens in a new tab.</p>
</aside>
<script>
(function(){var button=document.getElementById("menuButton"),menu=document.getElementById("sideMenu"),
scrim=document.getElementById("sideScrim"),close=document.getElementById("sideClose");
if(!button||!menu)return;
function set(open){menu.classList.toggle("open",open);scrim.hidden=!open;
menu.setAttribute("aria-hidden",String(!open));button.setAttribute("aria-expanded",String(open));
if(open)close.focus();}
button.addEventListener("click",function(){set(!menu.classList.contains("open"))});
close.addEventListener("click",function(){set(false);button.focus()});
scrim.addEventListener("click",function(){set(false)});
document.addEventListener("keydown",function(event){if(event.key==="Escape"&&menu.classList.contains("open")){set(false);button.focus()}});
document.getElementById("nationalGridLink").addEventListener("click",function(){
document.getElementById("nationalGridNote").hidden=false});})();
</script>
<div class="page-loading" id="pageLoading" role="status" hidden>Loading. Market data can take up to 30 seconds.</div>
<script>
(function(){var notice=document.getElementById("pageLoading");if(!notice)return;
window.addEventListener("beforeunload",function(){notice.hidden=false});
window.addEventListener("pageshow",function(){notice.hidden=true});})();
</script>"""
