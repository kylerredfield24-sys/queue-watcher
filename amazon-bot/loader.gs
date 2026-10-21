/**
 * Guardian Monitor – Amazon alerts LOADER (paste this once, then never touch it again).
 * The real bot code lives on GitHub (amazon-bot/amazon_bot.js) and is pulled in automatically,
 * so updates happen without you. Your webhook stays here, in your Google account only.
 *
 * After pasting: pick "setup" in the dropdown at the top -> Run -> Allow.
 */

// ===== SETTINGS (yours, private) =====
const CONFIG = {
  WEBHOOK_URL:     'PASTE_YOUR_AMAZON_WEBHOOK_HERE',
  AMAZON_ROLE_ID:  '1562193718092632065',   // @amazon
  POKEMON_ROLE_ID: '1558918973842661506',   // @Pokémon
  BOT_NAME:        'Guardian Monitor'
};
// =====================================

const CODE_URL = 'https://raw.githubusercontent.com/kylerredfield24-sys/queue-watcher/main/amazon-bot/amazon_bot.js';

function bot_() {
  const cache = CacheService.getScriptCache();
  let code = cache.get('code');
  if (!code) {
    try {
      const r = UrlFetchApp.fetch(CODE_URL + '?t=' + Date.now(), { muteHttpExceptions: true });
      if (r.getResponseCode() === 200 && r.getContentText().indexOf('checkKeepa') > -1) {
        code = r.getContentText();
        cache.put('code', code, 600);          // re-check GitHub every 10 minutes
        cache.put('lastgood', code, 21600);    // fallback if GitHub is down
      }
    } catch (e) {}
    if (!code) code = cache.get('lastgood');
    if (!code) throw new Error('Could not load the bot code from GitHub.');
  }
  return new Function('CONFIG', code)(CONFIG);
}

// These are what you (and the every-minute timer) run. Don't rename them.
function setup()        { CacheService.getScriptCache().remove('code'); bot_().setup(); }
function checkKeepa()   { bot_().checkKeepa(); }
function previewAlert() { bot_().previewAlert(); }
function stopAll()      { ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t)); }
