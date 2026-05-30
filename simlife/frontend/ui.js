/**
 * SimLife UI Manager v2
 */

const UI = {
  $city: null,
  $weekday: null,
  $time: null,
  $weather: null,
  $moodEmoji: null,
  $moodBar: null,
  $moodValue: null,
  $activityText: null,
  $sceneTag: null,
  $logList: null,
  $setupOverlay: null,
  $mainUi: null,
  $userOverlay: null,
  $userEnterBtn: null,
  _userProfile: null,

  _worlds: [],
  _selectedWorldId: 'modern',

  init() {
    this.$city = document.getElementById('disp-city');
    this.$weekday = document.getElementById('disp-weekday');
    this.$time = document.getElementById('disp-time');
    this.$weather = document.getElementById('disp-weather');
    this.$moodEmoji = document.getElementById('mood-emoji');
    this.$moodBar = document.getElementById('mood-bar');
    this.$moodValue = document.getElementById('mood-value');
    this.$activityText = document.getElementById('activity-text');
    this.$sceneTag = document.getElementById('scene-tag');
    this.$logList = document.getElementById('log-list');
    this.$setupOverlay = document.getElementById('setup-overlay');
    this.$mainUi = document.getElementById('main-ui');
    this.$userOverlay = document.getElementById('user-overlay');
    this.$userEnterBtn = document.getElementById('user-enter-btn');

    // Load user enter status + world list on startup
    this._loadUserProfile();
    this._loadWorlds();
  },

  async _loadWorlds() {
    try {
      const resp = await fetch('/api/worlds');
      if (resp.ok) {
        const data = await resp.json();
        this._worlds = data.worlds || [];
        this._selectedWorldId = data.current || 'modern';
        this._refreshWorldSelector();
      }
    } catch (e) { /* ignore */ }
  },

  _refreshWorldSelector() {
    const sel = document.getElementById('inp-world');
    if (!sel) return;
    // Clear and rebuild options
    const currentValue = sel.value;
    sel.innerHTML = '<option value="modern">🏢 Modern City (Default)</option>';
    for (const w of this._worlds) {
      if (w.world_id === 'modern') continue;
      const opt = document.createElement('option');
      opt.value = w.world_id;
      const typeEmoji = { fantasy: '🗡️', scifi: '🚀', xianxia: '⛩️', post_apocalyptic: '☢️', custom: '🌈' };
      opt.textContent = (typeEmoji[w.world_type] || '🌍') + ' ' + w.world_name;
      sel.appendChild(opt);
    }
    sel.innerHTML += '<option value="__ai_generate">🤖 AI Generate Custom World...</option>';
    sel.innerHTML += '<option value="__import">📋 Import World JSON...</option>';
    sel.value = this._selectedWorldId || 'modern';
    onWorldChange();
  },

  async _loadUserProfile() {
    try {
      const resp = await fetch('/api/user/profile');
      if (resp.ok) {
        this._userProfile = await resp.json();
        this._updateEnterButton();
      }
    } catch (e) { /* ignore */ }
  },

  _updateEnterButton() {
    if (!this.$userEnterBtn || !this._userProfile) return;
    const entered = this._userProfile.entered;
    const name = this._userProfile.name || 'You';
    const relation = this._userProfile.relation || '';

    if (entered) {
      this.$userEnterBtn.className = 'entered';
      this.$userEnterBtn.innerHTML = '<span id="user-status-dot" class="active"></span>' + name + ' (' + relation + ') present';
    } else if (relation) {
      this.$userEnterBtn.className = '';
      this.$userEnterBtn.innerHTML = '🏠 Enter World';
    } else {
      this.$userEnterBtn.className = '';
      this.$userEnterBtn.innerHTML = '🏠 Set Identity';
    }
  },

  showSetup() {
    this.$setupOverlay.style.display = 'flex';
    this.$mainUi.style.display = 'none';
  },

  hideSetup() {
    this.$setupOverlay.style.display = 'none';
    this.$mainUi.style.display = 'flex';
  },

  updateTopBar(data) {
    if (data.city) this.$city.textContent = data.city;
    if (data.weekday) this.$weekday.textContent = data.weekday;
    if (data.time) this.$time.textContent = data.time;
    if (data.weather) this.$weather.textContent = data.weather;
  },

  updateMood(mood) {
    const pct = Math.max(0, Math.min(100, mood));
    this.$moodBar.style.width = pct + '%';

    let color;
    if (pct >= 70) color = 'var(--mood-good)';
    else if (pct >= 40) color = 'var(--mood-mid)';
    else color = 'var(--mood-bad)';
    this.$moodBar.style.background = color;

    let emoji;
    if (pct >= 85) emoji = '😄';
    else if (pct >= 70) emoji = '😊';
    else if (pct >= 55) emoji = '🙂';
    else if (pct >= 40) emoji = '😐';
    else if (pct >= 25) emoji = '😔';
    else emoji = '😢';
    this.$moodEmoji.textContent = emoji;

    if (this.$moodValue) this.$moodValue.textContent = pct;
  },

  updateActivity(text, sceneLabel) {
    this.$activityText.textContent = text || '';
    if (this.$sceneTag && sceneLabel) {
      this.$sceneTag.textContent = sceneLabel;
    }
  },

  updateLogs(logs) {
    if (!logs || logs.length === 0) return;

    const existing = this.$logList.children.length;
    const newLogs = logs.slice(existing);

    for (const log of newLogs) {
      const item = document.createElement('div');
      item.className = 'log-item';
      item.innerHTML = `<span class="log-time">${log.time}</span><span class="log-event">${log.event}</span>`;
      this.$logList.appendChild(item);
    }

    const panel = document.getElementById('log-panel');
    panel.scrollTop = panel.scrollHeight;
  },

  clearLogs() {
    this.$logList.innerHTML = '';
  },

  setSetupStatus(text) {
    document.getElementById('setup-status').textContent = text;
  },

  setGenerateButton(enabled) {
    document.getElementById('btn-generate').disabled = !enabled;
  },
};

// Expose global functions
function skipSetup() {
  UI.hideSetup();
}

function toggleAllLogs() {
  // TODO: expand all logs popup
}

async function generateWorld() {
  const anchor = {
    character_name: document.getElementById('inp-name').value.trim(),
    city: document.getElementById('inp-city').value,
    occupation_hint: document.getElementById('inp-occupation').value.trim(),
    age: parseInt(document.getElementById('inp-age').value) || 24,
    personality_word: document.getElementById('inp-personality').value.trim(),
  };

  if (!anchor.character_name) {
    UI.setSetupStatus('Please enter character name');
    return;
  }

  UI.setSetupStatus('Generating character card and world... AI may take 10-30 seconds');
  UI.setGenerateButton(false);

  try {
    const resp = await fetch('/api/setup/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ anchor }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || 'Generation failed');
    }

    const data = await resp.json();
    UI.setSetupStatus('World generation complete!');

    setTimeout(() => {
      UI.hideSetup();
      if (typeof Game !== 'undefined') {
        Game.onCharacterReady(data.card);
      }
    }, 800);

  } catch (e) {
    UI.setSetupStatus('❌ ' + e.message);
    UI.setGenerateButton(true);
  }
}

/* --- Settings Menu --- */

function toggleSettingsMenu() {
  const menu = document.getElementById('settings-menu');
  menu.classList.toggle('show');
  if (menu.classList.contains('show')) {
    setTimeout(() => {
      document.addEventListener('click', _closeSettingsOnOutsideClick);
    }, 0);
  }
}

function _closeSettingsOnOutsideClick(e) {
  const menu = document.getElementById('settings-menu');
  const btn = document.getElementById('settings-btn');
  if (!menu.contains(e.target) && !btn.contains(e.target)) {
    menu.classList.remove('show');
    document.removeEventListener('click', _closeSettingsOnOutsideClick);
  }
}

function openSetupForReinit() {
  document.getElementById('settings-menu').classList.remove('show');
  if (!confirm('Reinitializing will delete the current character and world. Are you sure you want to start over?')) return;
  fetch('/api/reset', { method: 'POST' })
    .then(r => r.json())
    .then(() => { location.reload(); })
    .catch(e => { alert('Reset failed: ' + e.message); });
}

function openUserPanelFromMenu() {
  document.getElementById('settings-menu').classList.remove('show');
  const overlay = document.getElementById('user-overlay');
  if (overlay.style.display === 'flex') {
    closeUserPanel();
    return;
  }
  if (UI._userProfile) {
    document.getElementById('inp-user-name').value = UI._userProfile.name || '';
    document.getElementById('inp-user-relation').value = UI._userProfile.relation || '';
    document.getElementById('inp-user-role').value = UI._userProfile.world_role || '';
  }
  const hasRelation = UI._userProfile && UI._userProfile.relation;
  const btnEnter = document.getElementById('btn-user-enter');
  const btnLeave = document.getElementById('btn-user-leave');
  if (UI._userProfile && UI._userProfile.entered) {
    btnEnter.textContent = '✨ Save Changes';
    btnLeave.style.display = '';
  } else {
    btnEnter.textContent = hasRelation ? '✨ Enter World' : '✨ Save and Enter';
    btnLeave.style.display = 'none';
  }
  overlay.style.display = 'flex';
}

function doResetSimLife() {
  document.getElementById('settings-menu').classList.remove('show');
  if (!confirm('This will clear all SimLife data (character, world, NPCs, user identity). Are you sure?')) return;
  fetch('/api/reset', { method: 'POST' })
    .then(r => r.json())
    .then(() => { location.reload(); })
    .catch(e => { alert('Reset failed: ' + e.message); });
}

/* --- User Entry Management --- */

function toggleUserPanel() {
  const overlay = document.getElementById('user-overlay');
  if (overlay.style.display === 'flex') {
    closeUserPanel();
    return;
  }

  // Entered state: open panel to modify identity or leave
  // (no longer directly execute leave, let user choose in panel)
  
  // Fill existing info
  if (UI._userProfile) {
    document.getElementById('inp-user-name').value = UI._userProfile.name || '';
    document.getElementById('inp-user-relation').value = UI._userProfile.relation || '';
    document.getElementById('inp-user-role').value = UI._userProfile.world_role || '';
  }

  const hasRelation = UI._userProfile && UI._userProfile.relation;
  const btnEnter = document.getElementById('btn-user-enter');
  const btnLeave = document.getElementById('btn-user-leave');

  if (UI._userProfile && UI._userProfile.entered) {
    btnEnter.textContent = '✨ Save Changes';
    btnLeave.style.display = '';
  } else {
    btnEnter.textContent = hasRelation ? '✨ Enter World' : '✨ Save and Enter';
    btnLeave.style.display = 'none';
  }

  overlay.style.display = 'flex';
}

function closeUserPanel() {
  document.getElementById('user-overlay').style.display = 'none';
}

async function doUserEnter() {
  const name = document.getElementById('inp-user-name').value.trim();
  const relation = document.getElementById('inp-user-relation').value.trim();
  const worldRole = document.getElementById('inp-user-role').value.trim();

  if (!relation) {
    alert('Please fill in your relationship with the character');
    return;
  }

  try {
    // Save identity info first
    await fetch('/api/user/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, relation, world_role: worldRole }),
    });

    // Then enter world
    const resp = await fetch('/api/user/enter', { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || 'Enter failed');
    }

    UI._userProfile = { name, relation, world_role: worldRole, entered: true };
    UI._updateEnterButton();
    closeUserPanel();

  } catch (e) {
    alert('Operation failed: ' + e.message);
  }
}

async function doUserLeave() {
  if (!confirm('Are you sure you want to leave SimLife world?')) return;

  try {
    const resp = await fetch('/api/user/leave', { method: 'POST' });
    if (resp.ok && UI._userProfile) {
      UI._userProfile.entered = false;
      UI._updateEnterButton();
    }
  } catch (e) {
    alert('Operation failed: ' + e.message);
  }
}

/* --- World Management --- */

function onWorldChange() {
  const sel = document.getElementById('inp-world');
  const val = sel.value;

  const modernFields = document.getElementById('modern-fields');
  const cwFields = document.getElementById('custom-world-fields');
  const aiPanel = document.getElementById('ai-gen-world-panel');
  const importPanel = document.getElementById('import-world-panel');
  const infoBar = document.getElementById('world-info-bar');

  // Default: hide all
  modernFields.style.display = 'none';
  cwFields.style.display = 'none';
  aiPanel.style.display = 'none';
  importPanel.style.display = 'none';
  infoBar.style.display = 'none';

  if (val === 'modern') {
    modernFields.style.display = '';
  } else if (val === '__ai_generate') {
    aiPanel.style.display = '';
    cwFields.style.display = '';
  } else if (val === '__import') {
    importPanel.style.display = '';
    cwFields.style.display = '';
  } else {
    // Existing custom worlds
    cwFields.style.display = '';
    // Show world summary
    const world = UI._worlds.find(w => w.world_id === val);
    if (world) {
      infoBar.style.display = '';
      const typeNames = { fantasy: 'Fantasy Magic', scifi: 'Sci-Fi Future', xianxia: 'Xianxia Cultivation', post_apocalyptic: 'Post-Apocalyptic', custom: 'Custom' };
      infoBar.textContent = '🌍 ' + world.world_name + '  |  Type: ' + (typeNames[world.world_type] || world.world_type);
    }
  }

  // Sync name field
  const nameModern = document.getElementById('inp-name');
  const nameCw = document.getElementById('inp-name-cw');
  if (nameModern && nameCw) {
    if (val === 'modern') {
      nameCw.value = nameModern.value;
    } else {
      nameModern.value = nameCw.value;
    }
  }
}

async function doSwitchWorld(worldId) {
  try {
    const resp = await fetch('/api/worlds/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ world_id: worldId }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || 'Switch failed');
    }
    UI._selectedWorldId = worldId;
    UI.setSetupStatus('Switched to ' + worldId);
  } catch (e) {
    UI.setSetupStatus('❌ ' + e.message);
  }
}

async function doAIGenerateWorld() {
  const worldType = document.getElementById('inp-ai-world-type').value;
  const theme = document.getElementById('inp-ai-world-theme').value.trim();
  const role = document.getElementById('inp-ai-world-role').value.trim();

  if (!theme) {
    UI.setSetupStatus('Please fill in at least the Core Theme');
    return;
  }

  UI.setSetupStatus('AI is generating world setting... This may take 30-60 seconds');
  const btn = document.querySelector('#ai-gen-world-panel button');
  btn.disabled = true;
  btn.textContent = 'Generating...';

  try {
    const resp = await fetch('/api/worlds/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        world_type: worldType,
        core_theme: theme,
        character_role_hint: role,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || 'Generation failed');
    }

    const data = await resp.json();
    UI.setSetupStatus('World "' + (data.world_name || data.world_id) + '" generated successfully!');

    // Refresh list and select
    UI._selectedWorldId = data.world_id;
    await UI._loadWorlds();

  } catch (e) {
    UI.setSetupStatus('❌ ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '🧙 AI Generate World Setting (~30-60 sec)';
  }
}

async function doImportWorld() {
  const jsonStr = document.getElementById('inp-import-json').value.trim();
  if (!jsonStr) {
    UI.setSetupStatus('Please paste world JSON');
    return;
  }

  let setting;
  try {
    setting = JSON.parse(jsonStr);
  } catch (e) {
    UI.setSetupStatus('JSON parse error: ' + e.message);
    return;
  }

  UI.setSetupStatus('Importing...');

  try {
    const resp = await fetch('/api/worlds/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ setting }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || 'Import failed');
    }

    const data = await resp.json();
    UI.setSetupStatus('World "' + (data.world_name || data.world_id) + '" imported successfully!');

    UI._selectedWorldId = data.world_id;
    await UI._loadWorlds();

  } catch (e) {
    UI.setSetupStatus('❌ ' + e.message);
  }
}

// Rewrite generateWorld to support non-modern worlds
const _originalGenerateWorld = generateWorld;
window.generateWorld = async function() {
  const sel = document.getElementById('inp-world');
  const isModern = sel.value === 'modern';

  // If custom world selected but not actually switched
  if (!isModern && sel.value !== '__ai_generate' && sel.value !== '__import') {
    await doSwitchWorld(sel.value);
  }

  const anchor = {
    character_name: isModern
      ? document.getElementById('inp-name').value.trim()
      : document.getElementById('inp-name-cw').value.trim(),
    city: isModern ? document.getElementById('inp-city').value : '',
    occupation_hint: isModern
      ? document.getElementById('inp-occupation').value.trim()
      : document.getElementById('inp-occupation-cw').value.trim(),
    age: parseInt(isModern
      ? document.getElementById('inp-age').value
      : document.getElementById('inp-age-cw').value) || 24,
    personality_word: isModern
      ? document.getElementById('inp-personality').value.trim()
      : document.getElementById('inp-personality-cw').value.trim(),
  };

  if (!anchor.character_name) {
    UI.setSetupStatus('Please enter character name');
    return;
  }

  UI.setSetupStatus('Generating character card and world... AI may take 10-30 seconds');
  UI.setGenerateButton(false);

  try {
    const resp = await fetch('/api/setup/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ anchor }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || 'Generation failed');
    }

    const data = await resp.json();
    UI.setSetupStatus('World generation complete!');

    setTimeout(() => {
      UI.hideSetup();
      if (typeof Game !== 'undefined') {
        Game.onCharacterReady(data.card);
      }
    }, 800);

  } catch (e) {
    UI.setSetupStatus('❌ ' + e.message);
    UI.setGenerateButton(true);
  }
};

// --- Story Archive ---
function openStoryArchive() {
  document.getElementById('settings-menu').classList.remove('show');
  const overlay = document.getElementById('archive-overlay');
  overlay.style.display = 'flex';
  loadArchiveList();
}

function closeStoryArchive() {
  document.getElementById('archive-overlay').style.display = 'none';
}

async function loadArchiveList() {
  const listEl = document.getElementById('archive-list');
  listEl.innerHTML = 'Loading...';
  try {
    const resp = await fetch('/api/story/archive');
    const data = await resp.json();
    const archives = data.archives || [];
    if (archives.length === 0) {
      listEl.innerHTML = '<div style="padding:40px 0;color:#8899aa;">No archives yet. They will auto-generate after running one day in alternate world mode.</div>';
      return;
    }
    let html = '';
    for (const a of archives) {
      html += `<div class="archive-item" onclick="loadArchiveDay('${a.date}')"
        style="cursor:pointer;padding:12px 14px;margin-bottom:8px;background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.05);border-radius:10px;transition:background 0.2s;"
        onmouseover="this.style.background='rgba(255,255,255,0.08)'"
        onmouseout="this.style.background='rgba(0,0,0,0.2)'">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <span style="color:#e0e0e0;font-weight:bold;">📅 ${a.date}</span>
          <span style="font-size:12px;color:#8899aa;">${a.node_count} nodes · Mood ${a.mood}/100</span>
        </div>
        <div style="font-size:12px;color:#8899aa;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${a.summary || 'No records'}</div>
      </div>`;
    }
    listEl.innerHTML = html;
  } catch (e) {
    listEl.innerHTML = `<div style="padding:40px 0;color:#e94560;">Load failed: ${e.message}</div>`;
  }
}

async function loadArchiveDay(dateStr) {
  const listEl = document.getElementById('archive-list');
  listEl.innerHTML = 'Loading...';
  try {
    const resp = await fetch(`/api/story/archive/${dateStr}`);
    const data = await resp.json();
    let html = `<div style="margin-bottom:12px;">
      <button onclick="loadArchiveList()" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.08);color:#8899aa;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px;">← Back to List</button>
      <span style="color:#e0e0e0;font-weight:bold;font-size:15px;margin-left:10px;">📅 ${dateStr}</span>
    </div>`;
    // logs
    const logs = data.today_log || [];
    if (logs.length > 0) {
      html += '<div style="margin-bottom:12px;"><div style="font-size:12px;color:#8899aa;margin-bottom:6px;">📋 Logs</div>';
      for (const log of logs) {
        html += `<div style="padding:4px 0;font-size:13px;color:#e0e0e0;border-bottom:1px solid rgba(255,255,255,0.03);">
          <span style="color:#e94560;opacity:0.7;width:42px;display:inline-block;">${log.time || ''}</span>
          ${log.event || ''}
        </div>`;
      }
      html += '</div>';
    }
    // story nodes
    const plan = data.day_plan || [];
    if (plan.length > 0) {
      html += '<div style="font-size:12px;color:#8899aa;margin-bottom:6px;">📖 Story Nodes</div>';
      for (const node of plan) {
        html += `<div style="padding:10px 12px;margin-bottom:6px;background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.05);border-radius:8px;">
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:4px;">
            <span style="color:#e94560;font-size:12px;font-weight:bold;">${node.time || ''}</span>
            <span style="color:#4ecca3;font-size:12px;">${node.label || ''}</span>
            <span style="color:#8899aa;font-size:11px;">${node.scene || ''}</span>
          </div>`;
        if (node.expanded) {
          html += `<div style="font-size:13px;color:#e0e0e0;line-height:1.6;padding:6px 0;">${node.expanded}</div>`;
        } else if (node.activity) {
          html += `<div style="font-size:12px;color:#8899aa;">${node.activity}</div>`;
        }
        html += '</div>';
      }
    }
    listEl.innerHTML = html;
  } catch (e) {
    listEl.innerHTML = `<div style="padding:40px 0;color:#e94560;">Load failed: ${e.message}</div>`;
  }
}
