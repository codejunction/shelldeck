// cliamp radio (radio.cliamp.stream): the browser fetches the station list and plays the stream itself;
// nothing goes through shelldeck's server.
import { $, menu, toastError } from "./ui.js";

const STATIONS = "https://radio.cliamp.stream/stations";
const audio = new Audio();
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
  menu(anchor, [
    { header: "cliamp radio" },
    ...(playing ? [{ label: `Stop ${playing.name}`, icon: "x", onClick: stop }, "sep"] : []),
    ...stations.map((s) => ({ label: s.name, hint: s.genre === s.name ? "" : s.genre, icon: s.id === playing?.id ? "play" : undefined, onClick: () => play(s) })),
  ]);
}
