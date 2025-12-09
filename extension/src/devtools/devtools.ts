/**
 * DevTools page - creates the WebMap Archiver panel.
 */

chrome.devtools.panels.create(
  "WebMap Archiver",
  "icons/icon-16.png",
  "panel.html",
  (panel) => {
    console.log("[WebMap Archiver] DevTools panel created");
  }
);
