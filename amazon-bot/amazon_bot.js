/**
 * Guardian Monitor – Amazon (Keepa -> Gmail -> Discord) bot code.
 * This file is loaded by the small "loader" script in your Google Apps Script project.
 * Edit THIS file on GitHub to change the bot; the loader picks up changes within ~10 minutes.
 * No webhooks or secrets in here – those live only in the loader (in your Google account).
 * CONFIG is passed in by the loader: { WEBHOOK_URL, AMAZON_ROLE_ID, POKEMON_ROLE_ID, BOT_NAME }
 */
const BOT_VERSION = '1.1';
const SEARCH = 'from:keepa.com newer_than:2d';

// Every tracked product: ASIN -> [name, MSRP]
const PRODUCTS = {
  B0GYVHLP4L: ['Mega Evolution—Pitch Black Elite Trainer Box', 49.99],
  B0GR6N18F6: ['Mega Evolution—Chaos Rising Elite Trainer Box', 49.99],
  B0G3CY83L5: ['Mega Evolution—Ascended Heroes Elite Trainer Box', 49.99],
  B0FPM3LQJ4: ['Mega Evolution—Phantasmal Flames Elite Trainer Box', 49.99],
  B0FH5P7BWK: ['Mega Evolution Elite Trainer Box (Mega Lucario)', 49.99],
  B0FH5MH29W: ['Mega Evolution Elite Trainer Box (Mega Gardevoir)', 49.99],
  B0GFZV1ZVV: ['Mega Evolution—Perfect Order Elite Trainer Box', 49.99],
  B0GGCLDXZL: ['Mega Evolution—Perfect Order Elite Trainer Box', 49.99],
  B0HG3MQDWP: ['Mega Evolution—Delta Reign Elite Trainer Box', 49.99],
  B0H78BB9TY: ['30th Celebration Elite Trainer Box', 49.99],
  B0GG11K25W: ['Mega Evolution—Perfect Order Booster Bundle', 26.94],
  B0GYTRYV7P: ['Mega Evolution—Pitch Black Booster Bundle', 26.94],
  B0GR6Q72ND: ['Mega Evolution—Chaos Rising Booster Bundle', 26.94],
  B0FH5N9RH4: ['Mega Evolution Booster Bundle', 26.94],
  B0G3CV6Z9D: ['Mega Evolution—Ascended Heroes Booster Bundle', 26.94],
  B0FPLLR939: ['Mega Evolution—Phantasmal Flames Booster Bundle', 26.94],
  B0FTF6TC43: ['Mega Evolution—Phantasmal Flames Booster Bundle', 26.94],
  B0HG3RVZLW: ['Mega Evolution—Delta Reign Booster Bundle', 26.94],
  B0H783FY5Z: ['30th Celebration Booster Bundle', 26.94],
  B0GG16Q4X1: ['Mega Evolution—Perfect Order Booster Display Box (36 packs)', 161.64],
  B0GZ9S9KDR: ['Mega Evolution—Pitch Black Booster Display Box (36 packs)', 161.64],
  B0HGTBCCXX: ['Mega Evolution—Delta Reign Booster Display Box (36 packs)', 161.64],
  B0GSJMQ3QQ: ['Mega Evolution—Chaos Rising Booster Display Box (36 packs)', 161.64],
  B0FQFSYJWH: ['Mega Evolution Booster Display Box (36 packs)', 161.64],
  B0GYV6R6ZB: ['Mega Evolution—Pitch Black Build & Battle Box', 21.99],
  B0GGCDPJG9: ['Mega Evolution—Perfect Order Build & Battle Box', 21.99],
  B0GR6TRHX6: ['Mega Evolution—Chaos Rising Build & Battle Box', 21.99],
  B0HG47DGH4: ['Mega Evolution—Delta Reign Build & Battle Box', 21.99],
  B0GR6M4JV3: ['Mega Evolution—Chaos Rising 3-Pack Blister', 14.99],
  B0HG3JBNBN: ['Mega Evolution—Delta Reign 3-Pack Blister', 14.99],
  B0FPNRG8GS: ['Mega Evolution—Phantasmal Flames 3-Pack Blister (Sneasel)', 14.99],
  B0FPNSRRHQ: ['Mega Evolution—Phantasmal Flames 3-Pack Blister (Weavile)', 14.99],
  B0FH5P36ZR: ['Mega Evolution 3-Pack Blister (Golduck)', 14.99],
  B0GYTWKXTR: ['Mega Evolution—Pitch Black 3-Pack Blister', 14.99],
  B0GGCCLCJM: ['Mega Evolution—Perfect Order 3-Pack Blister (Chikorita)', 14.99],
  B0FJX86YTG: ['Mega Latias ex Box', 21.99],
  B0FPDP9WZW: ['Mega Kangaskhan ex Box', 21.99],
  B0G72PSB7D: ['Ascended Heroes Mega Feraligatr ex Box', 21.99],
  B0G72QBTBJ: ['Ascended Heroes Mega Emboar ex Box', 21.99],
  B0G78HDPPR: ['Ascended Heroes Mega Starter ex Box', 21.99],
  B0G72SPRP6: ['Ascended Heroes Mega Meganium ex Box', 21.99],
  B0H7817G9M: ['30th Celebration Sylveon ex Box', 21.99],
  B0H78194FC: ['30th Celebration Greninja ex Box', 21.99],
  B0GXJ8CSTY: ['Mega Greninja ex Premium Collection', 39.99],
  B0FPDLQ43H: ['Mega Venusaur ex Premium Collection', 39.99],
  B0FPDLTVVV: ['Mega Charizard X ex Ultra-Premium Collection', 129.99],
  B0GV6WRPRV: ['Mega Moonlit Tin', 26.99],
  B0G5S61BHK: ['Mega Charizard X Tin', 26.99],
  B0G5S8VQHV: ['Mega Charizard Tin', 26.99],
  B0G5FWJ8NL: ['Mega Charizard Tin', 26.99],
  B0H6DTXPK1: ['Ascended Heroes Tin', 26.99],
  B0FQP3YQ47: ['Poké Ball Tin', 14.99],
  B0G3CRP2NZ: ['Ascended Heroes Mini Tin', 9.99],
  B0H7F8BHC3: ['30th Celebration Mini Tin', 9.99],
  B0FPCRFCKB: ['Mega Heroes Mini Tin', 9.99],
  B0G3CPRRCT: ['Ascended Heroes Tech Sticker Collection (Charmander)', 14.99],
  B0G4XK6SVT: ['Ascended Heroes Tech Sticker Collection', 14.99],
  B0G3CS9CLQ: ['Ascended Heroes Tech Sticker Collection (Gastly)', 14.99],
  B0H77VZBX4: ['30th Celebration Tech Sticker Collection', 14.99],
  B0H8T2LY44: ['30th Celebration Tech Sticker Collection (Lucario)', 14.99],
  B0H77W4411: ['30th Celebration Poster Collection', 14.99],
  B0H8T1JGDG: ['30th Celebration Poster Collection', 14.99],
  B0G4XJPN8Q: ['Ascended Heroes Premium Poster Collection', 39.99],
  B0FN9Q8JBL: ['Mega Lucario ex Figure Collection', 29.99],
  B0GRCDKMSW: ['Mega Lucario ex League Battle Deck', 29.99],
  B0HCMFKYSZ: ['Mega Lucario ex League Battle Deck', 29.99],
  B0H7FDBNSB: ['30th Celebration Knock Out Collection', 9.99],
  B0H7FR54KK: ['30th Celebration Battle Deck', 19.99],
  B0H8SXPN9P: ['30th Celebration 2-Pack Blister', 9.99],
  B0GW2DK37Q: ['First Partner Illustration Collection—Series 2', 14.99],
  B0H2JVZYZZ: ['First Partner Illustration Collection—Series 3', 14.99]
};

function setup() {
  stopAll();
  ScriptApp.newTrigger('checkKeepa').timeBased().everyMinutes(1).create();
  markAllSeen_();   // old Keepa emails don't get posted
  post_({ content: '\u{1F9EA} TEST – Amazon alerts are connected. (No ping)',
          allowed_mentions: { parse: [] } });
}

function previewAlert() {
  post_({
    content: '\u{1F9EA} PREVIEW (no ping)\n\u{1F6D2} **In stock at Amazon**\n**Pokémon TCG: ' + PRODUCTS.B0GYVHLP4L[0] +
      '**\n\u{1F4B5} $49.99 (MSRP $49.99)\n\u{1F517} https://www.amazon.com/dp/B0GYVHLP4L',
    allowed_mentions: { parse: [] }
  });
}

function stopAll() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
}

function checkKeepa() {
  const props = PropertiesService.getScriptProperties();
  const seen = JSON.parse(props.getProperty('seen') || '{}');
  const threads = GmailApp.search(SEARCH, 0, 30);
  let changed = false;

  threads.forEach(th => th.getMessages().forEach(msg => {
    const id = msg.getId();
    if (seen[id]) return;
    seen[id] = Date.now();
    changed = true;

    const subject = msg.getSubject();
    const body = msg.getPlainBody() + ' ' + msg.getBody();
    const asins = unique_(body.match(/\b(B0[A-Z0-9]{8})\b/g) || []);
    if (!asins.length) return;                 // not a product alert (newsletter etc.)
    if (/lightning deal/i.test(subject)) return;

    const asin = asins.find(a => PRODUCTS[a]);
    if (!asin) return;                         // only post products on our list
    const info = PRODUCTS[asin];
    const name = 'Pokémon TCG: ' + info[0];
    const msrp = info[1];

    // Same rule as the store bots: alert up to $10 over MSRP, but never more than 25% over.
    const cap = maxPrice_(msrp);
    const prices = (body.match(/\$\s?\d{1,4}(?:,\d{3})*\.\d{2}/g) || [])
      .map(p => parseFloat(p.replace(/[$,\s]/g, '')))
      .filter(p => p > 0 && p <= cap);
    if (!prices.length) return;                // over the price limit (or no price found): no alert
    const price = Math.min.apply(null, prices);

    const roles = [CONFIG.AMAZON_ROLE_ID, CONFIG.POKEMON_ROLE_ID].filter(r => /^\d+$/.test(r || ''));
    post_({
      content: roles.map(r => '<@&' + r + '>').join(' ') +
        ' \u{1F6D2} **In stock at Amazon**\n**' + name + '**' +
        (price ? '\n\u{1F4B5} $' + price.toFixed(2) + (msrp ? ' (MSRP $' + msrp.toFixed(2) + ')' : '') : '') +
        '\n\u{1F517} https://www.amazon.com/dp/' + asin,
      allowed_mentions: { roles: roles }
    });
  }));

  if (changed) props.setProperty('seen', JSON.stringify(prune_(seen)));
}

function markAllSeen_() {
  const seen = {};
  GmailApp.search(SEARCH, 0, 50).forEach(th =>
    th.getMessages().forEach(m => seen[m.getId()] = Date.now()));
  PropertiesService.getScriptProperties().setProperty('seen', JSON.stringify(seen));
}

function post_(payload) {
  payload.username = CONFIG.BOT_NAME || 'Guardian Monitor';
  UrlFetchApp.fetch(CONFIG.WEBHOOK_URL, {
    method: 'post', contentType: 'application/json',
    payload: JSON.stringify(payload), muteHttpExceptions: true
  });
}

function maxPrice_(msrp) {   // MSRP + the smaller of $10 or 25% of MSRP
  return Math.round((msrp + Math.min(10, msrp * 0.25)) * 100) / 100;
}

function unique_(a) { return a.filter((x, i) => a.indexOf(x) === i); }

function prune_(seen) {   // keep 7 days of IDs
  const cut = Date.now() - 7 * 864e5;
  Object.keys(seen).forEach(k => { if (seen[k] < cut) delete seen[k]; });
  return seen;
}

return { setup: setup, checkKeepa: checkKeepa, previewAlert: previewAlert, stopAll: stopAll, version: BOT_VERSION };
