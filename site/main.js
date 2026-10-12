// shelldeck site motion: GSAP + ScrollTrigger + SplitText, Lenis for smooth scroll.
// Everything degrades to a static page when the CDN scripts fail or the user prefers reduced motion.
const $ = (s, el = document) => el.querySelector(s)
const $$ = (s, el = document) => [...el.querySelectorAll(s)]

// copy buttons and install tabs work without any animation library
$$('.copy').forEach(b => b.addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(b.dataset.copy) } catch { return }
  b.classList.add('done'); $('i', b).textContent = 'Copied'
  setTimeout(() => { b.classList.remove('done'); $('i', b).textContent = 'Copy' }, 1600)
}))
$$('.tabs button').forEach(t => t.addEventListener('click', () => {
  $$('.tabs button').forEach(x => x.classList.toggle('on', x === t))
  $$('.installs .cmd').forEach(c => { c.hidden = c.dataset.tab !== t.dataset.tab })
}))
// marquee loops by translating -50%, so it needs its content twice
const track = $('.track'); track.innerHTML += track.innerHTML
// pointer glow on feature cards
$$('.card').forEach(c => c.addEventListener('pointermove', e => {
  const r = c.getBoundingClientRect()
  c.style.setProperty('--x', `${e.clientX - r.left}px`); c.style.setProperty('--y', `${e.clientY - r.top}px`)
}))

const TERM = [
  ['c', "# who's working in this project"], ['p', 'sd agents'],
  ['c', "# read another terminal's screen"], ['p', 'sd peek Lina -n 20'],
  ['c', '# ask it to do something'], ['p', 'sd tell Lina "run the tests and fix what fails"'],
  ['c', '# a helper in a new pane'], ['p', 'sd spawn "write tests for auth.py" --model small'],
  ['c', '# hand work over, hear back when done'], ['p', 'sd handoff Kofi "review the diff"'],
  ['c', '# leave context for the next agent'], ['p', 'sd remember "integration tests need docker"'],
]
const termHTML = n => TERM.map(([k, s], i) => {
  const text = i < Math.floor(n) ? s : i === Math.floor(n) ? s.slice(0, Math.round((n % 1) * s.length)) : null
  if (text === null) return ''
  return k === 'p' ? `<span class="p">&gt;&gt;</span> ${esc(text)}\n` : `<span class="c">${esc(text)}</span>\n`
}).join('') + '<span class="caret"></span>'
const esc = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
if (!window.gsap || !window.ScrollTrigger || reduce) {
  $('#termOut').innerHTML = termHTML(TERM.length)
  $$('.tour .step').forEach(s => (s.style.opacity = 1))
  const view = $('.tour .view'), shot = $('.tour .shot'), fit = () => (shot.style.transform = `scale(${view.clientWidth / 1600})`)
  fit(); addEventListener('resize', fit)
} else {
  motion()
}

function motion() {
  gsap.registerPlugin(ScrollTrigger, ...(window.SplitText ? [SplitText] : []))

  if (window.Lenis) {  // drive Lenis from GSAP's ticker so ScrollTrigger never reads a stale position
    const lenis = new Lenis({ lerp: 0.1 })
    lenis.on('scroll', ScrollTrigger.update)
    gsap.ticker.add(t => lenis.raf(t * 1000))
    gsap.ticker.lagSmoothing(0)
    $$('a[href^="#"]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); lenis.scrollTo(a.getAttribute('href'), { offset: -80 }) }))
  }

  // headlines: words rise out of a mask
  document.fonts.ready.then(() => $$('.split').forEach((h, i) => {
    if (!window.SplitText) return
    const split = SplitText.create(h, { type: 'words', mask: 'words', wordsClass: 'sw' })
    const hero = h.closest('.hero')
    gsap.from(split.words, { yPercent: 110, duration: 1, stagger: .06, ease: 'power4.out', delay: hero ? .2 : 0,
      scrollTrigger: hero ? undefined : { trigger: h, start: 'top 85%' } })
  }))
  gsap.utils.toArray('.reveal').forEach(el => gsap.from(el, { y: 30, opacity: 0, duration: .9, ease: 'power3.out',
    delay: el.closest('.hero') ? .6 : 0, scrollTrigger: el.closest('.hero') ? undefined : { trigger: el, start: 'top 88%' } }))

  // hero: the app window lies back and stands up as you scroll; shell chips drift at different speeds
  gsap.fromTo('#heroWin', { rotateX: 28, scale: .88, y: 40 }, { rotateX: 0, scale: 1, y: 0, ease: 'none',
    scrollTrigger: { trigger: '.hero-shot', start: 'top 95%', end: 'top 25%', scrub: true } })
  gsap.from('.hero-shot', { opacity: 0, y: 120, duration: 1.4, ease: 'expo.out', delay: .5 })
  $$('.float').forEach((f, i) => {
    gsap.from(f, { opacity: 0, scale: .4, duration: .6, ease: 'back.out(2)', delay: 1 + i * .08 })
    gsap.to(f, { y: (i % 2 ? -1 : 1) * (60 + i * 25), ease: 'none', scrollTrigger: { trigger: '.hero-shot', start: 'top bottom', end: 'bottom top', scrub: true } })
  })

  const mm = gsap.matchMedia()
  mm.add('(min-width: 901px)', () => { tour(); horizontal() })
  mm.add('(max-width: 900px)', () => { fitTour(); $$('.tour .step').forEach(s => (s.style.opacity = 1, s.style.gridArea = 'auto', s.style.marginBottom = '32px')) })

  // feature cards and terminal typing
  gsap.from('.card', { y: 60, opacity: 0, duration: .8, ease: 'power3.out', stagger: { each: .06, from: 'start' }, scrollTrigger: { trigger: '.bento', start: 'top 80%' } })
  const out = $('#termOut'), state = { n: 0 }
  out.innerHTML = termHTML(0)
  gsap.to(state, { n: TERM.length, duration: 7, ease: 'none', onUpdate: () => (out.innerHTML = termHTML(state.n)), scrollTrigger: { trigger: '#term', start: 'top 70%' } })
  gsap.from('.big-logo', { scale: .3, rotate: -20, duration: 1.1, ease: 'back.out(1.7)', scrollTrigger: { trigger: '.install', start: 'top 70%' } })
}

// pinned tour: the window zooms to each part of the app while the copy changes
function tour() {
  const view = $('.tour .view'), shot = $('.tour .shot'), steps = $$('.tour .step')
  const imgs = $$('.shot img'), boxes = $$('.shot .box')
  const REGIONS = [{ x: 0, y: 170, w: 520, h: 292 }, { x: 936, y: 48, w: 662, h: 372 }, { x: 270, y: 476, w: 664, h: 374 }, { x: 296, y: 196, w: 760, h: 428 }]
  const zoom = r => { const W = view.clientWidth, H = view.clientHeight, s = Math.min(W / r.w, H / r.h); return { scale: s, x: W / 2 - (r.x + r.w / 2) * s, y: H / 2 - (r.y + r.h / 2) * s } }
  const full = () => zoom({ x: 0, y: 0, w: 1600, h: 900 })
  gsap.set(shot, full())
  gsap.set(imgs, { opacity: i => (i ? 0 : 1) })
  const tl = gsap.timeline({ defaults: { ease: 'power2.inOut' }, scrollTrigger: { trigger: '.tour', start: 'top top', end: '+=3200', pin: true, scrub: .6, invalidateOnRefresh: true } })
  REGIONS.forEach((r, i) => {
    const t = i * 2
    if (i) tl.to(steps[i - 1], { opacity: 0, y: -30, duration: .3 }, t).to(boxes[i - 1], { opacity: 0, duration: .3 }, t)
    if (i) tl.fromTo(steps[i], { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: .5 }, t + .4)
    else gsap.set(steps[0], { opacity: 1 })
    tl.to(shot, { scale: () => zoom(r).scale, x: () => zoom(r).x, y: () => zoom(r).y, duration: 1 }, t)
    imgs.forEach(img => tl.to(img, { opacity: +img.dataset.k === (i === 3 ? 3 : 0) ? 1 : 0, duration: .4 }, t))
    tl.fromTo(boxes[i], { opacity: 0, scale: 1.1 }, { opacity: 1, scale: 1, duration: .4 }, t + .9)
  })
  tl.to({}, { duration: .8 })
  ScrollTrigger.addEventListener('refreshInit', () => gsap.set(shot, full()))
}

// phones: no pinning, the whole screenshot fits the window
function fitTour() {
  const view = $('.tour .view'), fit = () => gsap.set('.tour .shot', { scale: view.clientWidth / 1600, x: 0, y: 0 })
  fit(); addEventListener('resize', fit)
}

// horizontal panels slide sideways while the section is pinned
function horizontal() {
  const track = $('.htrack')
  const dist = () => track.scrollWidth - innerWidth
  gsap.to(track, { x: () => -dist(), ease: 'none', scrollTrigger: { trigger: '.anywhere', start: 'top top', end: () => '+=' + dist(), pin: true, scrub: .6, invalidateOnRefresh: true } })
}
