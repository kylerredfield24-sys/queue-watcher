// Guardian Monitor window app.
// Built on your Mac the first time you open Guardian Monitor (with Apple's own osacompile),
// so it's a real Mac app: its own window, its own Dock icon, and ⌘Q to quit.
// Closing the window (red button) or ⌘Q quits everything.
ObjC.import("Cocoa");
ObjC.import("WebKit");

var BASE = "http://127.0.0.1:8765/";
var win = null;
var web = null;
var winDelegate = null;
var app = Application.currentApplication();
app.includeStandardAdditions = true;

ObjC.registerSubclass({
  name: "QWWindowDelegate",
  protocols: ["NSWindowDelegate"],
  methods: {
    // Closing the window quits the whole app, like a normal Mac app
    // (the watcher notices within a few seconds and shuts down too).
    "windowWillClose:": {
      types: ["void", ["id"]],
      implementation: function (notification) {
        quitWatcher();
        $.NSApp.terminate(null);
      }
    }
  }
});

// Tell the watcher to stop (uses a private key file only your Mac user can read).
function quitWatcher() {
  try {
    app.doShellScript('k=$(cat "$HOME/Library/Application Support/QueueWatcher/window.key" 2>/dev/null); ' +
      'curl -s -m 2 -X POST -H "X-QW-Key: $k" ' + BASE + 'quit_local >/dev/null 2>&1; true');
  } catch (e) {}
}

function serverUp() {
  try {
    app.doShellScript("curl -s -m 1 " + BASE + "ping");
    return true;
  } catch (e) {
    return false;
  }
}

function load() {
  var url = $.NSURL.URLWithString(BASE + "?app=1");
  web.loadRequest($.NSURLRequest.requestWithURL(url));
}

function showWindow() {
  if (win !== null) {
    if (win.isMiniaturized) win.deminiaturize(null);
    win.makeKeyAndOrderFront(null);
    $.NSApp.activateIgnoringOtherApps(true);
    return;
  }
  var frame = $.NSMakeRect(0, 0, 1180, 900);
  var style = $.NSWindowStyleMaskTitled | $.NSWindowStyleMaskClosable |
              $.NSWindowStyleMaskMiniaturizable | $.NSWindowStyleMaskResizable;
  win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
    frame, style, $.NSBackingStoreBuffered, false);
  win.releasedWhenClosed = false;
  win.title = "Guardian Monitor";
  win.minSize = $.NSMakeSize(420, 520);
  win.backgroundColor = $.NSColor.colorWithSRGBRedGreenBlueAlpha(0.024, 0.039, 0.075, 1.0);

  var config = $.WKWebViewConfiguration.alloc.init;
  config.mediaTypesRequiringUserActionForPlayback = 0;   // let video wallpapers play
  web = $.WKWebView.alloc.initWithFrameConfiguration(frame, config);
  web.autoresizingMask = $.NSViewWidthSizable | $.NSViewHeightSizable;
  win.contentView = web;

  winDelegate = $.QWWindowDelegate.alloc.init;
  win.delegate = winDelegate;

  win.center;
  win.setFrameAutosaveName("QueueWatcherMainWindow");   // remembers size and position
  load();
  win.makeKeyAndOrderFront(null);
  $.NSApp.activateIgnoringOtherApps(true);
}

function run() {
  // The launcher starts the watcher first; give it a moment if it's still starting.
  for (var i = 0; i < 40 && !serverUp(); i++) delay(0.25);
  showWindow();
}

// Opening Guardian Monitor again (or clicking the Dock icon) brings the window back.
function reopen() {
  showWindow();
}

// Keeps the app open in the background between events.
function idle() {
  return 60;
}
