// cliamp radio (radio.cliamp.stream): the browser fetches the station list and plays the stream itself;
// nothing goes through shelldeck's server.
import { $, menu, store, toastError } from "./ui.js";

const STATIONS = "https://radio.cliamp.stream/stations";
const audio = new Audio();
audio.volume = store.get("radioVolume", 0.7);
let stations = null;
let playing = null;

function show() {
  const b = $("#radio-btn");
  b.classList.toggle("on", !!playing);
  b.title = playing ? `cliamp radio: ${playing.name}` : "cliamp radio";
}

function play(s) {
  playing = s;
  audio.src = s.stream;
  audio.play().catch(() => {});
  show();
}

function stop() {
  playing = null;
  audio.pause();
  audio.removeAttribute("src"); // drops the live stream connection
  audio.load();
  show();
}

audio.addEventListener("error", () => {
  if (!playing) return;
  toastError(new Error(`${playing.name} could not be played`));
  stop();
});

export async function radioMenu(anchor) {
  try {
    if (!stations) {
      const r = await fetch(STATIONS);
      if (!r.ok) throw new Error();
      stations = (await r.json()).stations;
    }
  } catch {
    return toastError(new Error("cliamp radio is unreachable"));
  }
  const m = menu(anchor, [
    { header: "cliamp radio" },
    ...(playing ? [{ label: `Stop ${playing.name}`, icon: "x", onClick: stop }, "sep"] : []),
    ...stations.map((s) => ({ label: s.name, hint: s.genre === s.name ? "" : s.genre, icon: s.id === playing?.id ? "play" : undefined, onClick: () => play(s) })),
  ]);
  const vol = document.createElement("label");
  vol.className = "menu-volume";
  vol.innerHTML = `Volume <input type="range" min="0" max="1" step="0.05" aria-label="Radio volume">`;
  const input = vol.querySelector("input");
  input.value = audio.volume;
  input.addEventListener("input", () => {
    audio.volume = +input.value;
    try {
      store.set("radioVolume", audio.volume);
    } catch {} // private window: the volume just isn't remembered
  });
  m.firstElementChild.after(vol);
}
