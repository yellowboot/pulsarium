/* Pulsarium homepage: transparent light field, no external dependencies.
   Keep .pulsar's original markup and CSS for fallback and instant rollback. */
(function () {
  'use strict';
  const host = document.querySelector('.hero-visual[data-pulsar-webgl="light"]');
  if (!host || new URLSearchParams(window.location.search).get('pulsar') === 'original') return;
  if (!window.IntersectionObserver || !window.ResizeObserver || !window.MutationObserver) return;

  const vertexSource = `#version 300 es
  precision highp float;
  out vec2 vUv;
  void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    vUv = p;
    gl_Position = vec4(p * 2. - 1., 0., 1.);
  }`;
  const fragmentSource = `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 outColor;
uniform vec2 uResolution,uPointer;
uniform float uTime,uCalm;
const float PI=3.14159265359;
const float TAU=6.28318530718;
mat2 turn(float a){float c=cos(a),s=sin(a);return mat2(c,-s,s,c);}
void layer(inout vec3 color,inout float alpha,vec3 tint,float opacity){float a=clamp(opacity,0.,1.);color=tint*a+color*(1.-a);alpha=a+alpha*(1.-a);}
float gaussian(float d,float width){return exp(-d*d/(width*width));}
void main(){
 vec2 uv=(gl_FragCoord.xy-.5*uResolution)/uResolution.y;
 uv-=vec2(uPointer.x*.016,uPointer.y*.012);
 float t=uTime;
 vec3 violet=mix(vec3(.50,.32,1.),vec3(.29,.28,.84),uCalm);
 vec3 blue=mix(vec3(.05,.85,1.),vec3(.24,.48,.88),uCalm);
 vec3 rose=mix(vec3(.80,.28,1.),vec3(.64,.27,.67),uCalm);
 vec3 color=vec3(0.);float alpha=0.;
 float radius=length(uv);
 float pulse=.88+.12*sin(t*2.0);
 // The field is drawn as transparent light, with screen-space antialiasing.
 float px=1./uResolution.y;
 layer(color,alpha,violet,gaussian(radius,.17)*mix(.055,.025,uCalm)*pulse);
 float wave=fract(t*.16);
 float pulseRing=gaussian(radius-(.075+.24*wave),px*1.4)*pow(1.-wave,2.);
 layer(color,alpha,violet,pulseRing*mix(.24,.11,uCalm));
 float circle=gaussian(radius-.305,px*.65);
 layer(color,alpha,blue,circle*mix(.11,.050,uCalm));
 // The projected field contours change inclination as the magnetic axis rotates.
 for(int i=0;i<3;i++){
  float fi=float(i);
  float angle=-.50+fi*.65+.20*sin(t*.37+fi*1.9)+uPointer.x*.13;
  vec2 q=turn(angle)*uv;
  float minor=.48+.15*sin(t*.29+fi*2.1);
  vec2 e=vec2(q.x,q.y/minor);
  float target=.213+fi*.026;
  float distance=(length(e)-target)*minor;
  float front=.5+.5*q.y/(target*minor);
  float line=gaussian(distance,px*.65);
  float theta=atan(e.y,e.x);
  float moving=.5+.5*sin(theta*2.-t*1.20-fi*2.1);
  float strength=mix(.11,.27,clamp(front,0.,1.))*mix(1.4,1.,uCalm);
  layer(color,alpha,mix(violet,rose,fi*.34),line*strength);
  // A small light packet follows each contour; its tail dissolves along the field.
  float packet=pow(moving,35.);
  layer(color,alpha,mix(blue,violet,fi*.25),line*packet*.42);
  float trail=gaussian(distance,px*2.3)*pow(moving,13.);
  layer(color,alpha,violet,trail*mix(.065,.035,uCalm));
 }
 // The two polar beams precess in depth, with a narrow luminous spine and feathered edges.
 float phase=t*.34;
 vec3 axis=normalize(vec3(.72*cos(phase),.72*sin(phase),.42+.30*sin(phase+.7)));
 vec2 direction=normalize(turn(-.64)*axis.xy);
 float along=dot(uv,direction),across=dot(uv,vec2(-direction.y,direction.x));
 float distance=abs(along);
 float lengthFade=pow(max(0.,1.-distance/.51),1.4);
 float width=.0015+distance*.026;
 float beam=gaussian(across,width)*lengthFade;
 float haze=gaussian(across,width*4.5)*lengthFade;
 float segment=pow(.5+.5*sin(distance*67.-t*3.1),5.);
 float projection=.65+.35*length(axis.xy);
 layer(color,alpha,violet,haze*mix(.11,.070,uCalm)*projection);
 layer(color,alpha,mix(violet,blue,.20+.22*sin(t*.34)),beam*mix(.50,.36,uCalm)*projection);
 float spine=gaussian(across,.0007+distance*.0035)*lengthFade;
 layer(color,alpha,mix(vec3(.82,.90,1.),violet,.34),spine*(.22+segment*.20));
 // The star is a compact light source. Its outer corona stays transparent.
 layer(color,alpha,violet,gaussian(radius,.073)*mix(.28,.19,uCalm)*pulse);
 layer(color,alpha,mix(violet,blue,.40),gaussian(radius,.034)*.46*pulse);
 float edge=gaussian(radius,.0155);
 layer(color,alpha,mix(violet,vec3(1.),edge),gaussian(radius,.019)*.97);
 layer(color,alpha,vec3(1.),gaussian(radius,.0088)*.98);
 outColor=vec4(alpha>.00001?color/alpha:vec3(0.),alpha);
}`;

  const canvas = document.createElement('canvas');
  canvas.className = 'pulsar-light-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  host.appendChild(canvas);
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  const pixelBudget = window.innerWidth <= 600 ? 180000 : 480000;
  let gl, program, uniforms;
  let raf = 0, visible = false, lost = false, suspended = false, disposed = false;
  let dirty = true, width = 0, height = 0, time = 9, lastTime = 0, lastDraw = 0;
  let pointerX = 0, pointerY = 0, targetX = 0, targetY = 0;
  const removers = [];

  function listen(target, event, handler, options) {
    target.addEventListener(event, handler, options);
    removers.push(() => target.removeEventListener(event, handler, options));
  }
  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    lastTime = lastDraw = 0;
  }
  function active() {
    return !disposed && !lost && !suspended && visible && !document.hidden &&
      host.getAttribute('data-pulsar-webgl') === 'light';
  }
  function queue() {
    if (active() && !raf) raf = requestAnimationFrame(frame);
  }
  function releaseProgram() {
    if (gl && program && !gl.isContextLost()) gl.deleteProgram(program);
    program = null;
    uniforms = null;
  }
  function original() {
    host.classList.remove('is-pulsar-ready');
    stop();
  }
  function resize() {
    const rect = host.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    const budget = Math.min(pixelBudget, window.innerWidth <= 600 ? 180000 : 480000);
    const scale = Math.min(ratio, Math.sqrt(budget / Math.max(1, rect.width * rect.height)));
    const nextWidth = Math.max(2, Math.floor(rect.width * scale));
    const nextHeight = Math.max(2, Math.floor(rect.height * scale));
    if (width === nextWidth && height === nextHeight) return;
    width = canvas.width = nextWidth;
    height = canvas.height = nextHeight;
    dirty = true;
    queue();
  }
  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      console.warn('[Pulsarium pulsar] Shader compilation failed:', gl.getShaderInfoLog(shader));
      gl.deleteShader(shader);
      throw new Error('Pulsar shader unavailable');
    }
    return shader;
  }
  function initialize() {
    let vertex, fragment, nextProgram;
    try {
      gl = canvas.getContext('webgl2', {
        alpha: true, premultipliedAlpha: false, antialias: false,
        depth: false, stencil: false, preserveDrawingBuffer: false,
        powerPreference: 'low-power'
      });
      if (!gl || gl.isContextLost()) {
        console.warn('[Pulsarium pulsar] WebGL 2 unavailable; using the original SVG.');
        return false;
      }
      vertex = compile(gl.VERTEX_SHADER, vertexSource);
      fragment = compile(gl.FRAGMENT_SHADER, fragmentSource);
      nextProgram = gl.createProgram();
      gl.attachShader(nextProgram, vertex);
      gl.attachShader(nextProgram, fragment);
      gl.linkProgram(nextProgram);
      if (!gl.getProgramParameter(nextProgram, gl.LINK_STATUS)) {
        console.warn('[Pulsarium pulsar] Program link failed:', gl.getProgramInfoLog(nextProgram));
        throw new Error('Pulsar link unavailable');
      }
      program = nextProgram;
      uniforms = {};
      for (const name of ['uResolution', 'uPointer', 'uTime', 'uCalm']) {
        uniforms[name] = gl.getUniformLocation(program, name);
      }
      gl.useProgram(program);
      dirty = true;
      return true;
    } catch (error) {
      if (nextProgram) gl.deleteProgram(nextProgram);
      program = null;
      return false;
    } finally {
      if (vertex) gl.deleteShader(vertex);
      if (fragment) gl.deleteShader(fragment);
    }
  }
  function frame(stamp) {
    raf = 0;
    if (!host.isConnected) { dispose(); return; }
    if (!active()) { stop(); return; }
    if (!program && !initialize()) { dispose(); return; }
    if (lastDraw && stamp - lastDraw < 1000 / 30) { queue(); return; }
    const dt = lastTime ? Math.min((stamp - lastTime) / 1000, .08) : 0;
    lastTime = stamp;
    lastDraw = stamp;
    if (!motion.matches) {
      time += dt;
      const ease = 1 - Math.exp(-dt * 3);
      pointerX += (targetX - pointerX) * ease;
      pointerY += (targetY - pointerY) * ease;
    }
    if (dirty || !motion.matches) {
      gl.viewport(0, 0, width, height);
      gl.uniform2f(uniforms.uResolution, width, height);
      gl.uniform2f(uniforms.uPointer, pointerX, pointerY);
      gl.uniform1f(uniforms.uTime, time);
      gl.uniform1f(uniforms.uCalm, document.documentElement.getAttribute('data-color-theme') === 'calm' ? 1 : 0);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (gl.isContextLost()) return;
      if (!host.classList.contains('is-pulsar-ready')) {
        const error = gl.getError();
        if (error !== gl.NO_ERROR) {
          console.warn('[Pulsarium pulsar] First draw failed:', error);
          dispose();
          return;
        }
      }
      dirty = false;
      host.classList.add('is-pulsar-ready');
    }
    if (!motion.matches) queue();
  }
  function visibility() {
    host.classList.toggle('is-pulsar-idle', !visible || document.hidden || suspended);
    if (active()) { dirty = true; queue(); }
    else stop();
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    original();
    host.classList.remove('is-pulsar-idle');
    sizeObserver.disconnect();
    intersectionObserver.disconnect();
    themeObserver.disconnect();
    modeObserver.disconnect();
    for (const remove of removers) remove();
    releaseProgram();
    canvas.remove();
  }

  const sizeObserver = new ResizeObserver(resize);
  const intersectionObserver = new IntersectionObserver(entries => {
    visible = entries[0].isIntersecting;
    visibility();
  }, { threshold: .03 });
  const themeObserver = new MutationObserver(() => { dirty = true; queue(); });
  const modeObserver = new MutationObserver(() => {
    if (host.getAttribute('data-pulsar-webgl') !== 'light') dispose();
  });

  listen(document, 'visibilitychange', visibility);
  listen(window, 'pagehide', event => {
    if (event.persisted) { suspended = true; visibility(); }
    else dispose();
  });
  listen(window, 'pageshow', () => { suspended = false; visibility(); });
  listen(motion, 'change', () => {
    dirty = true;
    targetX = targetY = 0;
    if (motion.matches) pointerX = pointerY = 0;
    queue();
  });
  listen(canvas, 'webglcontextlost', event => {
    event.preventDefault();
    lost = true;
    program = uniforms = null;
    original();
  });
  listen(canvas, 'webglcontextrestored', () => {
    lost = false;
    dirty = true;
    queue();
  });
  if (finePointer) {
    listen(host, 'pointermove', event => {
      if (motion.matches) return;
      const rect = host.getBoundingClientRect();
      targetX = Math.max(-1, Math.min(1, (event.clientX - rect.left) / Math.max(rect.width, 1) * 2 - 1));
      targetY = Math.max(-1, Math.min(1, 1 - (event.clientY - rect.top) / Math.max(rect.height, 1) * 2));
    }, { passive: true });
    listen(host, 'pointerleave', () => { targetX = targetY = 0; }, { passive: true });
  }
  resize();
  sizeObserver.observe(host);
  intersectionObserver.observe(host);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-color-theme'] });
  modeObserver.observe(host, { attributes: true, attributeFilter: ['data-pulsar-webgl'] });
}());
