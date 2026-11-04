// Shared chart colours (from Binge Stats' charts.js), validated against the card surfaces
// (dark #11141d, light #ffffff). Colour follows the entity: give each series a fixed slot,
// never a colour by rank, so "Marc" is the same blue on every chart.
export const PALETTE = {
  dark:  ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
};
export const OTHER = { dark: "#5c6378", light: "#a3a9b8" };          // the "Other" bucket, always last
export const SEQUENTIAL = {                                          // heatmaps: one hue, low values recede
  dark:  ["#161c2b", "#104281", "#1c5cab", "#2a78d6", "#5598e7", "#9ec5f4"],
  light: ["#f2f6fc", "#cde2fb", "#9ec5f4", "#5598e7", "#256abf", "#104281"],
};
export const mode = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
export const colour = (slot, name) => (name === "Other" ? OTHER[mode()] : PALETTE[mode()][slot % 8]);
// Read a token for axis text, gridlines etc.: token("muted") -> "rgb(146,154,175)"
export function token(name) {
  const v = getComputedStyle(document.documentElement).getPropertyValue("--" + name).trim();
  return v ? `rgb(${v.split(/\s+/).join(",")})` : "#888";
}
