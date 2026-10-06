
// Every compact chart opens as a stateful analytical workbench.
(function () {
  var open = null;
  var previousFocus = null;
  var placeholder = null;

  function button(label, tool, title) {
    var b = document.createElement("button");
    b.type = "button"; b.textContent = label; b.dataset.tool = tool;
    if (title) b.title = title;
    return b;
  }

  function cssColor(panel, value) {
    if (!value) return "#4b8cff";
    if (value.slice(0, 2) !== "--") return value;
    return getComputedStyle(panel).getPropertyValue(value).trim() || "#4b8cff";
  }

  function finite(value) { return Number.isFinite(Number(value)); }
  function copyView(v) { return {xmin:v.xmin, xmax:v.xmax, ymin:v.ymin, ymax:v.ymax}; }
  function padded(lo, hi, ratio) {
    var span = hi - lo;
    if (!(span > 0)) span = Math.max(1, Math.abs(lo) * .1);
    return [lo - span * ratio, hi + span * ratio];
  }

  function numericWorkbench(panel, source, data) {
    var host = document.createElement("div");
    host.className = "pnl-workbench";
    host.innerHTML = '<div class="pnl-toolbar" role="toolbar" aria-label="Chart controls"></div>' +
      '<div class="pnl-legend" aria-label="Visible series"></div>' +
      '<div class="pnl-viewport"><canvas tabindex="0"></canvas>' +
      '<div class="pnl-tooltip" role="status" aria-live="polite"></div></div>' +
      '<div class="pnl-data" hidden></div>' +
      '<p class="pnl-keys">Wheel zooms X. Shift + wheel zooms Y. Drag pans. Shift + drag selects. ' +
      'Arrow keys pan; +/− zoom; R fits; G toggles grid; C toggles crosshair; L toggles log Y.</p>';
    panel.appendChild(host);

    var toolbar = host.querySelector(".pnl-toolbar");
    [
      ["Fit", "fit", "Fit all data"], ["X +", "xin", "Zoom X in"],
      ["X −", "xout", "Zoom X out"], ["Y +", "yin", "Zoom Y in"],
      ["Y −", "yout", "Zoom Y out"], ["Grid", "grid", "Toggle grid"],
      ["Crosshair", "cross", "Toggle crosshair"], ["Log Y", "log", "Toggle logarithmic Y axis"],
      ["Data", "data", "Show exact values"], ["CSV", "csv", "Download data"],
      ["PNG", "png", "Download current chart view"]
    ].forEach(function (spec) { toolbar.appendChild(button(spec[0], spec[1], spec[2])); });

    var canvas = host.querySelector("canvas"), ctx = canvas.getContext("2d");
    var tip = host.querySelector(".pnl-tooltip"), legend = host.querySelector(".pnl-legend");
    var table = host.querySelector(".pnl-data");
    var hidden = new Set(), state = {grid:true, cross:true, log:false, hover:null,
      drag:null, select:null, pointers:new Map(), pinch:null};
    var margin = {l:76, r:24, t:22, b:58};

    function pointsForBounds() {
      var all = [];
      (data.series || []).forEach(function (series, index) {
        if (!hidden.has(index)) (series.points || []).forEach(function (p) {
          if (finite(p[0]) && finite(p[1])) all.push([+p[0], +p[1]]);
        });
      });
      if (data.band) [data.band.low || [], data.band.high || []].forEach(function (line) {
        line.forEach(function (p) { if (finite(p[0]) && finite(p[1])) all.push([+p[0], +p[1]]); });
      });
      return all;
    }

    function fit() {
      var all = pointsForBounds();
      if (!all.length) all = [[0, 0], [1, 1]];
      var xs = all.map(function (p) { return p[0]; });
      var ys = all.map(function (p) { return p[1]; });
      if (state.log) ys = ys.filter(function (v) { return v > 0; });
      if (!ys.length) { state.log = false; ys = all.map(function (p) { return p[1]; }); }
      var xr = padded(Math.min.apply(null, xs), Math.max.apply(null, xs), .035);
      var yr = padded(Math.min.apply(null, ys), Math.max.apply(null, ys), .08);
      if (state.log) yr[0] = Math.max(yr[0], Math.min.apply(null, ys) * .55);
      state.base = {xmin:xr[0], xmax:xr[1], ymin:yr[0], ymax:yr[1]};
      state.view = copyView(state.base); draw();
    }

    function dims() {
      var box = canvas.getBoundingClientRect();
      return {w:Math.max(320, box.width), h:Math.max(260, box.height),
              pw:Math.max(1, box.width-margin.l-margin.r),
              ph:Math.max(1, box.height-margin.t-margin.b)};
    }
    function yt(v) { return state.log ? Math.log10(Math.max(v, Number.MIN_VALUE)) : v; }
    function yu(v) { return state.log ? Math.pow(10, v) : v; }
    function X(v, d) { return margin.l + (v-state.view.xmin)/(state.view.xmax-state.view.xmin)*d.pw; }
    function Y(v, d) {
      var lo=yt(state.view.ymin), hi=yt(state.view.ymax);
      return margin.t + (hi-yt(v))/(hi-lo)*d.ph;
    }
    function invX(px, d) { return state.view.xmin + (px-margin.l)/d.pw*(state.view.xmax-state.view.xmin); }
    function invY(py, d) {
      var lo=yt(state.view.ymin), hi=yt(state.view.ymax);
      return yu(hi-(py-margin.t)/d.ph*(hi-lo));
    }
    function fmt(v, precision) {
      if (!finite(v)) return "n/a";
      var a=Math.abs(v), d=precision === undefined ? (a < 10 ? 2 : a < 100 ? 1 : 0) : precision;
      if (a >= 1e9) return (v/1e9).toFixed(2)+"B";
      if (a >= 1e6) return (v/1e6).toFixed(2)+"M";
      if (a >= 1e3) return (v/1e3).toFixed(1)+"k";
      return Number(v).toLocaleString("en-GB", {maximumFractionDigits:d, minimumFractionDigits:d});
    }
    function niceTicks(lo, hi, count) {
      var span=Math.abs(hi-lo)||1, raw=span/count, power=Math.pow(10,Math.floor(Math.log10(raw)));
      var fraction=raw/power, nice=fraction<=1?1:fraction<=2?2:fraction<=5?5:10, step=nice*power;
      var first=Math.ceil(lo/step)*step, out=[];
      for(var v=first;v<=hi+step*.001&&out.length<20;v+=step) out.push(+v.toPrecision(12));
      return out;
    }
    function canvasPoint(event) {
      var r=canvas.getBoundingClientRect(); return {x:event.clientX-r.left, y:event.clientY-r.top};
    }
    function inPlot(p, d) {
      return p.x>=margin.l&&p.x<=d.w-margin.r&&p.y>=margin.t&&p.y<=d.h-margin.b;
    }

    function draw() {
      var rect=canvas.getBoundingClientRect(), ratio=Math.max(1, window.devicePixelRatio||1);
      var width=Math.max(320,rect.width), height=Math.max(260,rect.height);
      if(canvas.width!==Math.round(width*ratio)||canvas.height!==Math.round(height*ratio)){
        canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
      }
      ctx.setTransform(ratio,0,0,ratio,0,0); ctx.clearRect(0,0,width,height);
      var d=dims(), text=cssColor(panel,"--text-2"), faint=cssColor(panel,"--text-3");
      var sep=cssColor(panel,"--sep"), background=cssColor(panel,"--card");
      ctx.fillStyle=background;ctx.fillRect(0,0,d.w,d.h);
      ctx.save();ctx.beginPath();ctx.rect(margin.l,margin.t,d.pw,d.ph);ctx.clip();

      var xTicks=niceTicks(state.view.xmin,state.view.xmax,7);
      var yLo=yt(state.view.ymin),yHi=yt(state.view.ymax), yTicksT=niceTicks(yLo,yHi,6);
      if(state.grid){ctx.strokeStyle=sep;ctx.lineWidth=1;
        xTicks.forEach(function(v){var x=X(v,d);ctx.beginPath();ctx.moveTo(x,margin.t);ctx.lineTo(x,d.h-margin.b);ctx.stroke();});
        yTicksT.forEach(function(tv){var y=Y(yu(tv),d);ctx.beginPath();ctx.moveTo(margin.l,y);ctx.lineTo(d.w-margin.r,y);ctx.stroke();});
      }

      (data.guides||[]).forEach(function(g){ctx.save();ctx.strokeStyle=faint;ctx.setLineDash([5,5]);ctx.lineWidth=1;
        ctx.beginPath(); if(g.axis==="x"){var x=X(+g.value,d);ctx.moveTo(x,margin.t);ctx.lineTo(x,d.h-margin.b);}
        else{var y=Y(+g.value,d);ctx.moveTo(margin.l,y);ctx.lineTo(d.w-margin.r,y);}ctx.stroke();ctx.restore();});

      if(data.band && data.band.low && data.band.high){
        var low=data.band.low.filter(function(p){return finite(p[0])&&finite(p[1]);});
        var high=data.band.high.filter(function(p){return finite(p[0])&&finite(p[1]);});
        if(low.length&&high.length){ctx.beginPath();high.forEach(function(p,i){var x=X(+p[0],d),y=Y(+p[1],d);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
          low.slice().reverse().forEach(function(p){ctx.lineTo(X(+p[0],d),Y(+p[1],d));});ctx.closePath();
          ctx.globalAlpha=.14;ctx.fillStyle=cssColor(panel,data.band.color);ctx.fill();ctx.globalAlpha=1;}
      }

      (data.series||[]).forEach(function(series,index){
        if(hidden.has(index))return;var pts=(series.points||[]).filter(function(p){return finite(p[0])&&finite(p[1])&&(!state.log||+p[1]>0);});
        if(!pts.length)return;var colour=cssColor(panel,series.color);ctx.strokeStyle=colour;ctx.fillStyle=colour;
        if((data.area||series.area)&&data.kind!=="scatter"){ctx.beginPath();ctx.moveTo(X(+pts[0][0],d),d.h-margin.b);
          pts.forEach(function(p){ctx.lineTo(X(+p[0],d),Y(+p[1],d));});ctx.lineTo(X(+pts[pts.length-1][0],d),d.h-margin.b);
          ctx.closePath();ctx.globalAlpha=.12;ctx.fill();ctx.globalAlpha=1;}
        if(data.kind==="scatter"||series.pointsOnly){ctx.globalAlpha=.62;pts.forEach(function(p){var x=X(+p[0],d),y=Y(+p[1],d);
          if(x<margin.l-4||x>d.w-margin.r+4||y<margin.t-4||y>d.h-margin.b+4)return;
          ctx.beginPath();ctx.arc(x,y,series.radius||3,0,Math.PI*2);ctx.fill();});ctx.globalAlpha=1;}
        else{ctx.beginPath();ctx.lineWidth=series.width||2;ctx.lineJoin="round";ctx.lineCap="round";
          ctx.setLineDash(series.dash?[6,5]:[]);pts.forEach(function(p,i){var x=X(+p[0],d),y=Y(+p[1],d);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();ctx.setLineDash([]);}
      });

      if(state.select){ctx.fillStyle=cssColor(panel,"--blue");ctx.globalAlpha=.12;
        ctx.fillRect(Math.min(state.select.x0,state.select.x1),Math.min(state.select.y0,state.select.y1),
          Math.abs(state.select.x1-state.select.x0),Math.abs(state.select.y1-state.select.y0));ctx.globalAlpha=1;
        ctx.strokeStyle=cssColor(panel,"--blue");ctx.setLineDash([5,4]);ctx.strokeRect(Math.min(state.select.x0,state.select.x1),
          Math.min(state.select.y0,state.select.y1),Math.abs(state.select.x1-state.select.x0),Math.abs(state.select.y1-state.select.y0));ctx.setLineDash([]);}

      var nearest=null;
      if(state.cross&&state.hover&&inPlot(state.hover,d)){
        (data.series||[]).forEach(function(series,index){if(hidden.has(index))return;(series.points||[]).forEach(function(p){
          if(!finite(p[0])||!finite(p[1])||(state.log&&+p[1]<=0))return;var sx=X(+p[0],d),sy=Y(+p[1],d);
          var distance=Math.pow(sx-state.hover.x,2)+Math.pow(sy-state.hover.y,2);
          if(!nearest||distance<nearest.distance)nearest={distance:distance,x:sx,y:sy,p:p,series:series};});});
        if(nearest){ctx.strokeStyle=faint;ctx.lineWidth=1;ctx.setLineDash([3,4]);ctx.beginPath();ctx.moveTo(nearest.x,margin.t);
          ctx.lineTo(nearest.x,d.h-margin.b);ctx.moveTo(margin.l,nearest.y);ctx.lineTo(d.w-margin.r,nearest.y);ctx.stroke();ctx.setLineDash([]);
          ctx.fillStyle=cssColor(panel,nearest.series.color);ctx.beginPath();ctx.arc(nearest.x,nearest.y,4.5,0,Math.PI*2);ctx.fill();}
      }
      ctx.restore();

      ctx.strokeStyle=sep;ctx.beginPath();ctx.moveTo(margin.l,margin.t);ctx.lineTo(margin.l,d.h-margin.b);ctx.lineTo(d.w-margin.r,d.h-margin.b);ctx.stroke();
      ctx.font="11px system-ui,-apple-system,sans-serif";ctx.fillStyle=text;ctx.textAlign="center";
      xTicks.forEach(function(v){ctx.fillText(fmt(v)+(data.xSuffix||""),X(v,d),d.h-margin.b+19);});
      ctx.textAlign="right";yTicksT.forEach(function(tv){var v=yu(tv);ctx.fillText(fmt(v),margin.l-9,Y(v,d)+4);});
      ctx.font="600 11px system-ui,-apple-system,sans-serif";ctx.textAlign="center";ctx.fillText(data.xLabel||"X",margin.l+d.pw/2,d.h-10);
      var axisUnit=(data.ySuffix||"").trim();ctx.save();ctx.translate(15,margin.t+d.ph/2);ctx.rotate(-Math.PI/2);
      ctx.fillText(data.yAxisLabel||((data.yLabel||"Y")+(axisUnit?" ("+axisUnit+")":"")),0,0);ctx.restore();

      if(nearest){tip.hidden=false;tip.textContent=nearest.series.name+(nearest.p[2]?"\n"+nearest.p[2]:"")+"\n"+
          (data.xLabel||"X")+": "+fmt(+nearest.p[0],data.xPrecision)+(data.xSuffix||"")+"\n"+
          (data.yLabel||"Y")+": "+fmt(+nearest.p[1],data.precision)+(data.ySuffix||"");
        tip.style.left=Math.min(d.w-184,Math.max(8,nearest.x+13))+"px";tip.style.top=Math.max(8,nearest.y-30)+"px";
      }else tip.hidden=true;
    }

    function zoomX(factor, anchor) {var v=state.view,a=finite(anchor)?+anchor:(v.xmin+v.xmax)/2;
      v.xmin=a+(v.xmin-a)*factor;v.xmax=a+(v.xmax-a)*factor;draw();}
    function zoomY(factor, anchor) {var v=state.view,lo=yt(v.ymin),hi=yt(v.ymax),a=finite(anchor)?yt(+anchor):(lo+hi)/2;
      v.ymin=yu(a+(lo-a)*factor);v.ymax=yu(a+(hi-a)*factor);draw();}
    function panX(fraction){var span=state.view.xmax-state.view.xmin,shift=span*fraction;state.view.xmin+=shift;state.view.xmax+=shift;draw();}
    function panY(fraction){var lo=yt(state.view.ymin),hi=yt(state.view.ymax),shift=(hi-lo)*fraction;state.view.ymin=yu(lo+shift);state.view.ymax=yu(hi+shift);draw();}
    function setPressed(tool,on){var b=toolbar.querySelector('[data-tool="'+tool+'"]');if(b){b.classList.toggle("on",on);b.setAttribute("aria-pressed",String(on));}}

    function rebuildTable(){var rows=[];(data.series||[]).forEach(function(series){(series.points||[]).forEach(function(p){rows.push([series.name,p[0],p[1],p[2]||""]);});});
      var limit=Math.min(rows.length,1000), body="",details=rows.some(function(r){return r[3];});for(var i=0;i<limit;i++)body+="<tr><td>"+escapeText(rows[i][0])+"</td>"+(details?"<td>"+escapeText(rows[i][3])+"</td>":"")+"<td>"+escapeText(fmt(+rows[i][1],data.xPrecision)+(data.xSuffix||""))+"</td><td>"+escapeText(fmt(+rows[i][2],data.precision)+(data.ySuffix||""))+"</td></tr>";
      table.innerHTML='<div class="pnl-data-scroll"><table><thead><tr><th>Series</th>'+(details?'<th>'+escapeText(data.pointLabel||"Details")+'</th>':"")+'<th>'+escapeText(data.xLabel||"X")+'</th><th>'+escapeText(data.yLabel||"Y")+'</th></tr></thead><tbody>'+body+'</tbody></table></div>'+
        (rows.length>limit?'<p>Showing 1,000 of '+rows.length+' values. CSV contains all values.</p>':"");}
    function escapeText(value){var n=document.createElement("span");n.textContent=String(value);return n.innerHTML;}
    function download(name,blob){var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(function(){URL.revokeObjectURL(a.href);},0);}
    function csv(){var rows=[["series",data.pointLabel||"details",data.xLabel||"x",data.yLabel||"y"]];(data.series||[]).forEach(function(s){(s.points||[]).forEach(function(p){rows.push([s.name,p[2]||"",p[0],p[1]]);});});
      var content=rows.map(function(row){return row.map(function(v){return '"'+String(v).replace(/"/g,'""')+'"';}).join(",");}).join("\n");
      download((panel.id||"chart")+".csv",new Blob([content],{type:"text/csv;charset=utf-8"}));}
    function png(){canvas.toBlob(function(blob){if(blob)download((panel.id||"chart")+".png",blob);},"image/png");}

    (data.series||[]).forEach(function(series,index){var b=button(series.name,"legend");b.className="pnl-series";b.setAttribute("aria-pressed","true");
      var swatch=document.createElement("i");swatch.style.background=cssColor(panel,series.color);b.prepend(swatch);b.addEventListener("click",function(){
        hidden.has(index)?hidden.delete(index):hidden.add(index);b.classList.toggle("off",hidden.has(index));b.setAttribute("aria-pressed",String(!hidden.has(index)));draw();});legend.appendChild(b);});

    toolbar.addEventListener("click",function(event){var b=event.target.closest("button[data-tool]");if(!b)return;var tool=b.dataset.tool;
      if(tool==="fit")fit();else if(tool==="xin")zoomX(.72);else if(tool==="xout")zoomX(1/.72);
      else if(tool==="yin")zoomY(.72);else if(tool==="yout")zoomY(1/.72);
      else if(tool==="grid"){state.grid=!state.grid;setPressed("grid",state.grid);draw();}
      else if(tool==="cross"){state.cross=!state.cross;setPressed("cross",state.cross);draw();}
      else if(tool==="log"){var positives=pointsForBounds().filter(function(p){return p[1]>0;});if(!positives.length)return;
        state.log=!state.log;setPressed("log",state.log);fit();}
      else if(tool==="data"){table.hidden=!table.hidden;b.classList.toggle("on",!table.hidden);b.setAttribute("aria-pressed",String(!table.hidden));if(!table.hidden)rebuildTable();}
      else if(tool==="csv")csv();else if(tool==="png")png();});

    canvas.addEventListener("wheel",function(event){event.preventDefault();var p=canvasPoint(event),d=dims(),factor=Math.exp(event.deltaY*.0014);
      if(event.shiftKey)zoomY(factor,invY(p.y,d));else if(event.altKey||event.ctrlKey){zoomX(factor,invX(p.x,d));zoomY(factor,invY(p.y,d));}
      else zoomX(factor,invX(p.x,d));},{passive:false});
    canvas.addEventListener("dblclick",fit);
    canvas.addEventListener("pointerdown",function(event){if(event.button!==0)return;var p=canvasPoint(event),d=dims();if(!inPlot(p,d))return;
      canvas.setPointerCapture(event.pointerId);state.pointers.set(event.pointerId,p);
      if(state.pointers.size===2){var ps=Array.from(state.pointers.values());state.pinch={distance:Math.hypot(ps[0].x-ps[1].x,ps[0].y-ps[1].y),view:copyView(state.view),mid:{x:(ps[0].x+ps[1].x)/2,y:(ps[0].y+ps[1].y)/2}};state.drag=null;}
      else state.drag={x:p.x,y:p.y,view:copyView(state.view),box:event.shiftKey};});
    canvas.addEventListener("pointermove",function(event){var p=canvasPoint(event),d=dims();state.hover=p;if(state.pointers.has(event.pointerId))state.pointers.set(event.pointerId,p);
      if(state.pointers.size>=2&&state.pinch){var ps=Array.from(state.pointers.values()),distance=Math.hypot(ps[0].x-ps[1].x,ps[0].y-ps[1].y)||1;
        state.view=copyView(state.pinch.view);var f=state.pinch.distance/distance;zoomX(f,invX(state.pinch.mid.x,d));zoomY(f,invY(state.pinch.mid.y,d));return;}
      if(state.drag){if(state.drag.box){state.select={x0:state.drag.x,y0:state.drag.y,x1:Math.max(margin.l,Math.min(d.w-margin.r,p.x)),y1:Math.max(margin.t,Math.min(d.h-margin.b,p.y))};draw();}
        else{state.view=copyView(state.drag.view);var dx=p.x-state.drag.x,dy=p.y-state.drag.y;
          var sx=(state.view.xmax-state.view.xmin)*dx/d.pw;state.view.xmin-=sx;state.view.xmax-=sx;
          var lo=yt(state.view.ymin),hi=yt(state.view.ymax),sy=(hi-lo)*dy/d.ph;state.view.ymin=yu(lo+sy);state.view.ymax=yu(hi+sy);draw();}}
      else draw();});
    function pointerUp(event){var p=canvasPoint(event),d=dims();state.pointers.delete(event.pointerId);state.pinch=null;
      if(state.drag&&state.drag.box&&state.select&&Math.abs(state.select.x1-state.select.x0)>8&&Math.abs(state.select.y1-state.select.y0)>8){
        var s=state.select;state.view={xmin:invX(Math.min(s.x0,s.x1),d),xmax:invX(Math.max(s.x0,s.x1),d),
          ymin:invY(Math.max(s.y0,s.y1),d),ymax:invY(Math.min(s.y0,s.y1),d)};}
      state.drag=null;state.select=null;state.hover=p;draw();}
    canvas.addEventListener("pointerup",pointerUp);canvas.addEventListener("pointercancel",pointerUp);
    canvas.addEventListener("pointerleave",function(){if(!state.drag){state.hover=null;draw();}});
    canvas.addEventListener("keydown",function(event){var key=event.key.toLowerCase(),handled=true;
      if(key==="arrowleft")panX(-.08);else if(key==="arrowright")panX(.08);else if(key==="arrowup")panY(.08);else if(key==="arrowdown")panY(-.08);
      else if(key==="+"||key==="=")event.shiftKey?zoomY(.75):zoomX(.75);else if(key==="-"||key==="_")event.shiftKey?zoomY(1/.75):zoomX(1/.75);
      else if(key==="r")fit();else if(key==="g"){state.grid=!state.grid;setPressed("grid",state.grid);draw();}
      else if(key==="c"){state.cross=!state.cross;setPressed("cross",state.cross);draw();}
      else if(key==="l")toolbar.querySelector('[data-tool="log"]').click();else handled=false;if(handled)event.preventDefault();});

    setPressed("grid",true);setPressed("cross",true);fit();
    var observer=new ResizeObserver(function(){draw();});observer.observe(host.querySelector(".pnl-viewport"));
    return {focus:function(){canvas.focus();},destroy:function(){observer.disconnect();host.remove();}};
  }

  function diagramWorkbench(panel, source) {
    var host=document.createElement("div");host.className="pnl-workbench pnl-diagram-workbench";
    host.innerHTML='<div class="pnl-toolbar" role="toolbar" aria-label="Diagram controls"></div><div class="pnl-diagram-stage" tabindex="0"></div>'+
      '<p class="pnl-keys">Wheel or +/− zooms. Drag pans. Arrow keys pan. R fits the full allocation.</p>';
    panel.appendChild(host);var toolbar=host.querySelector(".pnl-toolbar"),stage=host.querySelector(".pnl-diagram-stage");
    [["Fit","fit"],["Zoom +","in"],["Zoom −","out"],["SVG","svg"]].forEach(function(s){toolbar.appendChild(button(s[0],s[1]));});
    var clone=source.cloneNode(true);clone.removeAttribute("id");clone.classList.add("pnl-diagram-svg");stage.appendChild(clone);
    var raw=(source.getAttribute("viewBox")||"0 0 1000 300").split(/\s+/).map(Number),base={x:raw[0],y:raw[1],w:raw[2],h:raw[3]},view={x:base.x,y:base.y,w:base.w,h:base.h},drag=null;
    function apply(){clone.setAttribute("viewBox",[view.x,view.y,view.w,view.h].join(" "));}
    function zoom(factor,px,py){var r=stage.getBoundingClientRect(),fx=px===undefined?.5:(px-r.left)/r.width,fy=py===undefined?.5:(py-r.top)/r.height;
      var nw=view.w*factor,nh=view.h*factor;view.x+=(view.w-nw)*fx;view.y+=(view.h-nh)*fy;view.w=nw;view.h=nh;apply();}
    toolbar.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;if(b.dataset.tool==="fit"){view={x:base.x,y:base.y,w:base.w,h:base.h};apply();}
      else if(b.dataset.tool==="in")zoom(.75);else if(b.dataset.tool==="out")zoom(1/.75);else if(b.dataset.tool==="svg"){
        var xml=new XMLSerializer().serializeToString(clone);var a=document.createElement("a");a.href=URL.createObjectURL(new Blob([xml],{type:"image/svg+xml"}));a.download=(panel.id||"diagram")+".svg";a.click();setTimeout(function(){URL.revokeObjectURL(a.href);},0);}});
    stage.addEventListener("wheel",function(e){e.preventDefault();zoom(Math.exp(e.deltaY*.0014),e.clientX,e.clientY);},{passive:false});
    stage.addEventListener("pointerdown",function(e){if(e.button!==0)return;stage.setPointerCapture(e.pointerId);drag={x:e.clientX,y:e.clientY,view:{x:view.x,y:view.y,w:view.w,h:view.h}};});
    stage.addEventListener("pointermove",function(e){if(!drag)return;var r=stage.getBoundingClientRect();view.x=drag.view.x-(e.clientX-drag.x)/r.width*drag.view.w;view.y=drag.view.y-(e.clientY-drag.y)/r.height*drag.view.h;apply();});
    stage.addEventListener("pointerup",function(){drag=null;});stage.addEventListener("pointercancel",function(){drag=null;});
    stage.addEventListener("keydown",function(e){var k=e.key.toLowerCase(),handled=true,dx=view.w*.08,dy=view.h*.08;
      if(k==="arrowleft")view.x-=dx;else if(k==="arrowright")view.x+=dx;else if(k==="arrowup")view.y-=dy;else if(k==="arrowdown")view.y+=dy;
      else if(k==="+"||k==="=")zoom(.75);else if(k==="-")zoom(1/.75);else if(k==="r")view={x:base.x,y:base.y,w:base.w,h:base.h};else handled=false;
      if(handled){e.preventDefault();apply();}});apply();return {focus:function(){stage.focus();},destroy:function(){host.remove();}};
  }

  function mount(panel) {
    var source=panel.querySelector("svg[data-inspector]")||panel.querySelector("svg");if(!source)return null;
    var raw=source.dataset.inspector||'{"kind":"diagram"}', data;try{data=JSON.parse(raw);}catch(error){data={kind:"diagram"};}
    var fingerprint=raw+(data.kind==="diagram"?source.innerHTML:"");
    if(panel.__workbench&&panel.__inspectorRaw===fingerprint)return panel.__workbench;
    if(panel.__workbench)panel.__workbench.destroy();panel.__inspectorRaw=fingerprint;
    panel.__workbench=data.kind==="diagram"?diagramWorkbench(panel,source):numericWorkbench(panel,source,data);
    return panel.__workbench;
  }

  function closePanel() {
    if(!open)return;var closing=open;closing.classList.remove("pnl-open");closing.removeAttribute("role");closing.removeAttribute("aria-modal");
    var expand=closing.querySelector(".pnl-expand-hint");if(expand)expand.setAttribute("aria-expanded","false");
    if(placeholder&&placeholder.parentNode){placeholder.parentNode.insertBefore(closing,placeholder);placeholder.remove();}
    else closing.remove();placeholder=null;document.body.classList.remove("pnl-lock");open=null;
    if(previousFocus&&previousFocus.focus&&document.contains(previousFocus))previousFocus.focus();
  }
  function openPanel(panel) {
    if(panel===open)return;closePanel();previousFocus=document.activeElement;open=panel;
    placeholder=document.createComment("expanded analytical panel");panel.parentNode.insertBefore(placeholder,panel);document.body.appendChild(panel);
    panel.classList.add("pnl-open");panel.setAttribute("role","dialog");panel.setAttribute("aria-modal","true");
    var expand=panel.querySelector(".pnl-expand-hint");if(expand)expand.setAttribute("aria-expanded","true");document.body.classList.add("pnl-lock");
    var workbench=mount(panel);requestAnimationFrame(function(){if(workbench)workbench.focus();else panel.querySelector(".pnl-close").focus();});
  }

  document.querySelectorAll(".pnl").forEach(function(panel,index){var heading=panel.querySelector("h3, h2"),title=heading?heading.textContent.trim():"analytical chart "+(index+1);
    panel.id=panel.id||"analytical-panel-"+(index+1);if(heading){heading.id=heading.id||panel.id+"-title";panel.setAttribute("aria-labelledby",heading.id);}
    var hint=button("Expand","expand");hint.className="pnl-expand-hint";hint.setAttribute("aria-label","Open interactive "+title);hint.setAttribute("aria-haspopup","dialog");hint.setAttribute("aria-controls",panel.id);hint.setAttribute("aria-expanded","false");panel.appendChild(hint);
    var close=button("Close","close");close.className="pnl-close";close.setAttribute("aria-label","Close expanded chart");close.addEventListener("click",function(e){e.stopPropagation();closePanel();});panel.appendChild(close);
    hint.addEventListener("click",function(e){e.stopPropagation();openPanel(panel);});panel.addEventListener("click",function(e){if(panel.classList.contains("pnl-open"))return;
      if(e.target.closest&&e.target.closest("button,a,input,select,textarea"))return;openPanel(panel);});
    panel.addEventListener("inspector:update",function(){if(panel===open){var workbench=mount(panel);requestAnimationFrame(function(){if(workbench)workbench.focus();});}});
  });
  document.addEventListener("keydown",function(event){if(event.key === "Escape"&&open){event.preventDefault();closePanel();return;}
    if(event.key === "Tab"&&open){var focusable=Array.from(open.querySelectorAll('button:not([disabled]),[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')).filter(function(el){return el.offsetParent!==null;});
      if(!focusable.length)return;var first=focusable[0],last=focusable[focusable.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
})();
