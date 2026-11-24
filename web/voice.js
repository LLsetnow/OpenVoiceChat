const element = (id) => document.getElementById(id);
const button = element("toggle");
const messages = element("messages");
const scroller = element("scroll");
const t = (key, params) => (window.ovcI18n ? window.ovcI18n.translate(key, params) : key);
const names = {
  get user() {
    return t("user_name");
  },
  assistant: window.ovcI18n ? window.ovcI18n.t("assistant_default_name") : "千夏",
};
const stageNames = ["asr", "llm", "tts"];
const charSelect = element("char-select");
const ttsEngineSelect = element("tts-engine-select");
const voiceSelect = element("voice-select");
const historyList = element("history-list");
const newConversationButton = element("new-conversation");
const langToggle = element("lang-toggle");
const avatars = () => document.querySelectorAll("[data-avatar]");
function setAvatar(src) {
  if (src && typeof src === "string" && src.startsWith("/assets/")) {
    src = src.slice(1);
  }
  for (const img of avatars()) img.src = src;
}
const charNameDisplay = element("char-name-display");

const isLocalServer = location.protocol !== "file:" && (location.port === "18768" || !location.port);
const apiBase = isLocalServer ? "" : "http://127.0.0.1:18768";
const wsHost = (isLocalServer && location.host) ? location.host : "127.0.0.1:18768";

let charactersData = [
  { id: "mio", name: "千夏", avatar: "assets/img/chinatsu.png", voice: "chinatsu", tts_engine: "gpt_sovits" }
];
let conversationData = [];
let currentConversationMessages = [];
let selectedConversationId = null;
let voicesData = {
  gpt_sovits: [
    { id: "chinatsu", name: "千夏" },
    { id: "ling", name: "铃" },
    { id: "zhe", name: "哲" },
    { id: "nangongyu", name: "南宫羽" },
    { id: "remielle", name: "雷米埃尔" }
  ],
  cosyvoice: [
    { id: "longxiaochun_v2", name: "龙小淳 (知性积极)" },
    { id: "longyue_v2", name: "龙悦 (温暖磁性)" },
    { id: "longhua_v2", name: "龙华 (元气甜美)" },
    { id: "longfeifei_v2", name: "龙菲菲 (甜美娇气)" },
    { id: "longwan_v2", name: "龙婉 (细腻柔声)" }
  ]
};

function getCharacterName(c) {
  if (!c) return t("assistant_default_name");
  if (c.name === "千夏" || c.id === "mio" || c.id === "example") {
    return t("assistant_default_name");
  }
  return c.name || c.id;
}

function getVoiceDisplayName(v) {
  const key = `voice_${v.id.replace(/_v2$/, "")}`;
  const translated = t(key);
  if (translated && translated !== key) {
    return translated;
  }
  return `${v.name} (${v.id})`;
}

async function fetchVoicesAndCharacters() {
  try {
    const resChars = await fetch(`${apiBase}/api/characters`);
    if (resChars.ok) {
      const data = await resChars.json();
      charactersData = data.characters || [];
      populateCharacters(charactersData);
    }
  } catch (e) {
    console.warn("fetch characters failed", e);
  }

  try {
    const resVoices = await fetch(`${apiBase}/api/voices`);
    if (resVoices.ok) {
      voicesData = await resVoices.json();
      updateVoiceOptions();
    }
  } catch (e) {
    console.warn("fetch voices failed", e);
  }

  await refreshConversationList();
  if (conversationData.length) {
    await openConversation(conversationData[0].id);
  }
}

function populateCharacters(chars) {
  if (!chars || chars.length === 0) return;
  charSelect.innerHTML = "";
  for (const c of chars) {
    const opt = document.createElement("option");
    opt.value = c.id;
    if (c.id === "mio") {
      opt.textContent = t("char_mio_desc");
    } else if (c.id === "example") {
      opt.textContent = t("char_example_desc");
    } else {
      const charName = getCharacterName(c);
      opt.textContent = `${charName} (${c.id})`;
    }
    charSelect.appendChild(opt);
  }
  applyCharacter(chars[0]);
}

function applyCharacter(c) {
  if (!c) return;
  names.assistant = getCharacterName(c);
  if (charNameDisplay) charNameDisplay.textContent = names.assistant;
  if (c.avatar) setAvatar(c.avatar);
  if (c.tts_engine) {
    ttsEngineSelect.value = c.tts_engine;
    updateVoiceOptions();
  }
  if (c.voice) {
    voiceSelect.value = c.voice;
  }
  const ttsSub = element("tts-sub");
  if (ttsSub) {
    const engineLabel = c.tts_engine === "cosyvoice" ? "CosyVoice" : "GPT-SoVITS";
    ttsSub.textContent = `${engineLabel} · ${names.assistant}`;
  }
  updateMicStatus();
}

function updateVoiceOptions() {
  const engine = ttsEngineSelect.value || "gpt_sovits";
  const list = voicesData[engine] || [];
  voiceSelect.innerHTML = "";
  for (const v of list) {
    const opt = document.createElement("option");
    opt.value = v.id;
    opt.textContent = getVoiceDisplayName(v);
    voiceSelect.appendChild(opt);
  }
}

if (charSelect) {
  charSelect.addEventListener("change", () => {
    const c = charactersData.find(item => item.id === charSelect.value);
    if (c) applyCharacter(c);
  });
}

if (ttsEngineSelect) {
  ttsEngineSelect.addEventListener("change", () => {
    updateVoiceOptions();
  });
}

const drafts = new Map();
const devices = { input: element("audio-input"), output: element("audio-output") };
const refresh = element("refresh-devices");
const voiceMode = element("voice-mode");
const voiceModeKey = "openvoicechat.audio.voice-mode";
const selectedDevices = { input: "", output: "" };

let deviceListReady = false;
let loadingDevices = false;
let deviceError = "";
let socket;
let state = "connecting";
let micPaused = false;
let waitingReply = false;
let scrollFrame;

// Web Audio & Recording state
let audioCtx = null;
let micStream = null;
let micSource = null;
let processor = null;
let isRecording = false;
let resampleOffset = 0;
const activeSources = new Set();
let nextPlayTime = 0;
let playStartTime = 0;
let isPlayingAudio = false;
let serverGenerating = false;
let hasPlayedAudioInTurn = false;
let inTailHangover = false;
let tailHangoverTimer = null;
const TAIL_HANGOVER_MS = 350;
const BARGE_IN_RMS_THRESHOLD = 0.035;

try {
  const savedMode = localStorage.getItem(voiceModeKey);
  if (savedMode === "speaker" || savedMode === "barge_in") {
    voiceMode.value = savedMode;
  } else {
    // 默认选用扬声器防回声模式（最适合免提外放，保证不误断）
    voiceMode.value = "speaker";
  }
} catch {
  // Keep the HTML default if browser storage isn't available.
}

voiceMode.addEventListener("change", () => {
  try {
    localStorage.setItem(voiceModeKey, voiceMode.value);
  } catch {}
  updateMicStatus();
});

for (const [kind, select] of Object.entries(devices)) {
  const key = `openvoicechat.audio.${kind}`;
  try {
    selectedDevices[kind] = localStorage.getItem(key) ?? "";
  } catch {}
  select.addEventListener("change", async () => {
    selectedDevices[kind] = select.value;
    try {
      localStorage.setItem(key, select.value);
    } catch {}
    if (kind === "output") {
      await applyOutputDevice(select.value);
    }
    updateControls();
  });
}

async function applyOutputDevice(deviceId) {
  if (!audioCtx || typeof audioCtx.setSinkId !== "function") return;
  try {
    await audioCtx.setSinkId(deviceId || "");
  } catch (err) {
    console.warn("audioCtx.setSinkId failed:", err);
  }
}

function updateControls() {
  const idle = state === "idle";
  const canChoose = idle && deviceListReady && !loadingDevices;
  const valid = Object.values(devices).every((select) => (
    select.selectedOptions.length > 0 && !select.selectedOptions[0].disabled
  ));
  button.disabled = state !== "running" && !(canChoose && valid);
  for (const select of Object.values(devices)) select.disabled = !canChoose;
  voiceMode.disabled = !idle;
  refresh.disabled = !idle || loadingDevices;
  refresh.textContent = loadingDevices ? t("refresh_identifying") : t("refresh_devices");
  const note = element("device-note");
  note.textContent = deviceError || (
    state === "running" ? t("device_note_running") :
      canChoose && !valid ? t("device_note_invalid") : ""
  );
  note.hidden = !note.textContent;
  if (newConversationButton) newConversationButton.disabled = state !== "idle";
  if (state === "idle") {
    element("toggle-label").textContent = selectedConversationId ? t("continue_chat") : t("start_chat");
  }
  if (historyList) {
    for (const item of historyList.querySelectorAll("button")) {
      item.disabled = state !== "idle";
    }
  }
}

async function refreshConversationList() {
  if (!historyList) return;
  try {
    const response = await fetch(`${apiBase}/api/conversations`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    conversationData = data.conversations || [];
    historyList.replaceChildren();
    if (!conversationData.length) {
      const empty = document.createElement("p");
      empty.className = "history-empty";
      empty.textContent = t("no_history");
      historyList.appendChild(empty);
      return;
    }

    for (const conversation of conversationData) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "history-item";
      item.dataset.active = String(conversation.id === selectedConversationId);

      const title = document.createElement("span");
      title.className = "history-item-title";
      title.textContent = !conversation.title || conversation.title === "新对话"
        ? t("new_conversation") : conversation.title;
      const preview = document.createElement("span");
      preview.className = "history-item-preview";
      preview.textContent = conversation.preview || "";
      const date = document.createElement("time");
      date.className = "history-item-date";
      date.dateTime = conversation.updated_at;
      date.textContent = new Date(conversation.updated_at).toLocaleString(window.ovcI18n.locale, {
        month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
      });
      item.append(title, preview, date);
      item.addEventListener("click", () => openConversation(conversation.id));
      historyList.appendChild(item);
    }
    updateControls();
  } catch (err) {
    console.warn("fetch conversation history failed", err);
    historyList.replaceChildren();
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = t("history_unavailable");
    historyList.appendChild(empty);
  }
}

async function openConversation(id) {
  if (state !== "idle" && state !== "connecting") return;
  try {
    const response = await fetch(`${apiBase}/api/conversations/${encodeURIComponent(id)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const conversation = await response.json();
    selectedConversationId = conversation.id;
    renderConversationMessages(conversation.messages || []);
    const character = charactersData.find(item => item.id === conversation.character_id);
    if (character) {
      charSelect.value = character.id;
      applyCharacter(character);
    }
    await refreshConversationList();
  } catch (err) {
    console.warn("open conversation failed", err);
    notice(t("history_open_failed"));
  }
}

function startNewConversation() {
  if (state !== "idle") return;
  selectedConversationId = null;
  renderConversationMessages([]);
  if (charactersData.length) {
    charSelect.value = charactersData[0].id;
    applyCharacter(charactersData[0]);
  }
  refreshConversationList();
  updateControls();
  notice();
}

newConversationButton?.addEventListener("click", startNewConversation);

function updateMicStatus() {
  const isAudioPlaying = typeof activeSources !== "undefined" && activeSources ? activeSources.size > 0 : false;
  const isInterSentencePause = typeof hasPlayedAudioInTurn !== "undefined" && hasPlayedAudioInTurn && typeof serverGenerating !== "undefined" && serverGenerating && !isAudioPlaying;
  const isSpeakerMode = typeof voiceMode !== "undefined" && voiceMode ? voiceMode.value === "speaker" : true;
  const inHangover = typeof inTailHangover !== "undefined" ? inTailHangover : false;
  const shouldPause = isSpeakerMode && (isAudioPlaying || isInterSentencePause || inHangover);

  const sessionStateEl = element("session-state");
  if (sessionStateEl) sessionStateEl.dataset.paused = String(shouldPause);

  const asstName = names.assistant || t("assistant_default_name");
  if (state === "running") {
    let label = t("turn_can_speak_lbl", { name: asstName });
    let hint = "";
    if (isSpeakerMode) {
      if (isAudioPlaying || isInterSentencePause) {
        label = t("turn_speaking_paused_lbl", { name: asstName });
        hint = t("turn_speaking_paused_hnt", { name: asstName });
      } else if (inTailHangover) {
        label = t("turn_absorbing_lbl", { name: asstName });
        hint = t("turn_absorbing_hnt", { name: asstName });
      } else if (waitingReply) {
        label = t("turn_thinking_lbl", { name: asstName });
        hint = t("turn_thinking_hnt", { name: asstName });
      } else {
        label = t("turn_can_speak_lbl", { name: asstName });
        hint = t("turn_can_speak_hnt", { name: asstName });
      }
    } else {
      // 智能免提打断模式
      if (isAudioPlaying) {
        label = t("turn_barge_speaking_lbl", { name: asstName });
        hint = t("turn_barge_speaking_hnt", { name: asstName });
      } else if (isInterSentencePause) {
        label = t("turn_barge_continuing_lbl", { name: asstName });
        hint = t("turn_barge_continuing_hnt", { name: asstName });
      } else if (inTailHangover) {
        label = t("turn_barge_absorbing_lbl", { name: asstName });
        hint = t("turn_barge_absorbing_hnt", { name: asstName });
      } else if (waitingReply) {
        label = t("turn_barge_thinking_lbl", { name: asstName });
        hint = t("turn_barge_thinking_hnt", { name: asstName });
      } else {
        label = t("turn_barge_listening_lbl", { name: asstName });
        hint = t("turn_barge_listening_hnt", { name: asstName });
      }
    }
    element("state-label").textContent = label;
    element("mic-hint").textContent = hint;
  } else {
    element("mic-hint").textContent = isSpeakerMode ?
      t("speaker_mode_title", { name: asstName }) :
      t("barge_mode_title", { name: asstName });
  }

  element("speaker-mode-hint").textContent = isSpeakerMode ?
    t("speaker_select_hint", { name: asstName }) :
    t("barge_select_hint", { name: asstName });
}

async function requestDevices() {
  deviceError = "";
  loadingDevices = true;
  updateControls();
  if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
    try {
      const devList = await navigator.mediaDevices.enumerateDevices();
      const inputs = devList.filter((d) => d.kind === "audioinput");
      const outputs = devList.filter((d) => d.kind === "audiooutput");

      const inputDevices = inputs.map((d, i) => ({
        id: d.deviceId,
        label: d.label || (inputs.length === 1 ? t("default_microphone") : t("microphone_indexed", { index: i + 1 })),
      }));
      const outputDevices = outputs.map((d, i) => ({
        id: d.deviceId,
        label: d.label || (outputs.length === 1 ? t("default_speaker") : t("speaker_indexed", { index: i + 1 })),
      }));

      const catalogue = {
        input: {
          default: inputDevices.length > 0 ? inputDevices[0].label : t("default_microphone"),
          devices: inputDevices,
        },
        output: {
          default: outputDevices.length > 0 ? outputDevices[0].label : t("default_speaker"),
          devices: outputDevices,
        },
      };
      showDevices(catalogue);
      return;
    } catch (err) {
      console.warn("Browser enumerateDevices error:", err);
    }
  }

  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ action: "devices" }));
  }
}

function showDevices(catalogue) {
  for (const [kind, select] of Object.entries(devices)) {
    const group = catalogue[kind];
    if (!group) continue;
    const defaultOption = new Option(
      group.default ? t("system_default_named", { name: group.default }) : t("system_default"),
      ""
    );
    const options = group.devices.map(({ id, label }) => new Option(label, id));
    let choice = selectedDevices[kind];
    if (choice && !group.devices.some(({ id }) => id === choice)) {
      choice = "";
      selectedDevices[kind] = "";
      try {
        localStorage.removeItem(`openvoicechat.audio.${kind}`);
      } catch {}
    }
    select.replaceChildren(defaultOption, ...options);
    select.value = choice || "";
  }
  deviceListReady = true;
  loadingDevices = false;
  deviceError = "";
  updateControls();
}

function resampleTo16k(input, inSampleRate) {
  if (inSampleRate === 16000) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return out;
  }
  const ratio = inSampleRate / 16000;
  const estimatedCount = Math.ceil((input.length - resampleOffset) / ratio);
  const out = new Int16Array(estimatedCount + 2);
  let count = 0;
  let pos = resampleOffset;
  while (pos < input.length) {
    const index = Math.floor(pos);
    const frac = pos - index;
    const nextIndex = index + 1 < input.length ? index + 1 : index;
    const s = input[index] * (1 - frac) + input[nextIndex] * frac;
    const clamped = Math.max(-1, Math.min(1, s));
    out[count++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF;
    pos += ratio;
  }
  resampleOffset = pos - input.length;
  return out.subarray(0, count);
}

function checkTurnCompletion() {
  if (serverGenerating) {
    // 服务端仍在生成后续句子（分句间停顿），保持抑制
    return;
  }
  if (activeSources.size > 0) {
    // 本地仍有音频块在播放
    return;
  }
  // 服务端本轮全部发完，且本地最后一个音频块也播完了！
  if (tailHangoverTimer) {
    clearTimeout(tailHangoverTimer);
  }
  inTailHangover = true;
  updateMicStatus();

  tailHangoverTimer = setTimeout(() => {
    tailHangoverTimer = null;
    inTailHangover = false;
    isPlayingAudio = false;
    hasPlayedAudioInTurn = false;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action: "playback_ended" }));
    }
    updateMicStatus();
  }, TAIL_HANGOVER_MS);
}

function queueAudioChunk(arrayBuffer) {
  if (!audioCtx) return;
  if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }
  const int16 = new Int16Array(arrayBuffer);
  if (int16.length === 0) return;

  if (tailHangoverTimer) {
    clearTimeout(tailHangoverTimer);
    tailHangoverTimer = null;
  }
  inTailHangover = false;
  hasPlayedAudioInTurn = true;
  waitingReply = false;

  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768.0;
  }
  const buffer = audioCtx.createBuffer(1, float32.length, 16000);
  buffer.copyToChannel(float32, 0);

  const source = audioCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(audioCtx.destination);

  const now = audioCtx.currentTime;
  const startTime = Math.max(now + 0.03, nextPlayTime);
  source.start(startTime);
  nextPlayTime = startTime + buffer.duration;

  const wasPlaying = activeSources.size > 0;
  activeSources.add(source);
  isPlayingAudio = true;
  if (!wasPlaying) {
    playStartTime = performance.now();
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action: "playback_started" }));
    }
  }
  updateMicStatus();

  source.onended = () => {
    activeSources.delete(source);
    if (activeSources.size === 0) {
      nextPlayTime = 0;
      checkTurnCompletion();
    }
    updateMicStatus();
  };
}

function stopAudioPlayback() {
  if (tailHangoverTimer) {
    clearTimeout(tailHangoverTimer);
    tailHangoverTimer = null;
  }
  inTailHangover = false;
  isPlayingAudio = false;
  serverGenerating = false;
  hasPlayedAudioInTurn = false;
  for (const source of activeSources) {
    try {
      source.stop();
      source.disconnect();
    } catch (e) {}
  }
  activeSources.clear();
  nextPlayTime = 0;
  updateMicStatus();
}

async function startAudioRecording() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === "suspended") {
    await audioCtx.resume();
  }

  await applyOutputDevice(selectedDevices.output);

  const constraints = {
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  };
  if (selectedDevices.input) {
    constraints.audio.deviceId = { ideal: selectedDevices.input };
  }

  micStream = await navigator.mediaDevices.getUserMedia(constraints);
  requestDevices();

  isRecording = true;
  resampleOffset = 0;

  micSource = audioCtx.createMediaStreamSource(micStream);
  processor = audioCtx.createScriptProcessor(2048, 1, 1);

  const silentGain = audioCtx.createGain();
  silentGain.gain.value = 0;
  processor.connect(silentGain);
  silentGain.connect(audioCtx.destination);

  processor.onaudioprocess = (e) => {
    if (!isRecording || !socket || socket.readyState !== WebSocket.OPEN) return;

    const inputData = e.inputBuffer.getChannelData(0);

    // 计算当前帧 RMS 能量
    let sum = 0;
    for (let i = 0; i < inputData.length; i++) {
      sum += inputData[i] * inputData[i];
    }
    const rms = Math.sqrt(sum / inputData.length);

    const isAudioPlaying = activeSources.size > 0;
    const isInterSentencePause = hasPlayedAudioInTurn && serverGenerating && !isAudioPlaying;
    const isSpeakerMode = voiceMode.value === "speaker";

    if (isSpeakerMode) {
      // 扬声器防回声模式：播放中、分句间停顿、播完尾音吸收期，丢弃麦克风输入
      if (isAudioPlaying || isInterSentencePause || inTailHangover) {
        return;
      }
    } else {
      // 智能免提打断模式：
      // 1. 如果处于尾音过滤窗口（播完 350ms 内）或分句等待间隙，坚决物理丢弃，防止尾音作为新发言被识别
      if (inTailHangover || isInterSentencePause) {
        return;
      }
      // 2. 如果正在发声播放，只有用户开口声量明显大于扬声器回声门限，才允许打断
      if (isAudioPlaying) {
        if (performance.now() - playStartTime < 300) {
          return; // 播放前 300ms 瞬态过滤
        }
        if (rms < BARGE_IN_RMS_THRESHOLD) {
          return; // 回声能量小，丢弃
        }
      }
    }

    const pcm16 = resampleTo16k(inputData, audioCtx.sampleRate);
    if (pcm16.length > 0) {
      socket.send(pcm16.buffer.slice(pcm16.byteOffset, pcm16.byteOffset + pcm16.byteLength));
    }
  };

  micSource.connect(processor);
}

function stopAudioRecording() {
  isRecording = false;
  resampleOffset = 0;
  serverGenerating = false;
  hasPlayedAudioInTurn = false;
  waitingReply = false;
  if (processor) {
    try {
      processor.disconnect();
    } catch (e) {}
    processor = null;
  }
  if (micSource) {
    try {
      micSource.disconnect();
    } catch (e) {}
    micSource = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }
  stopAudioPlayback();
}

function setState(next) {
  state = next;
  if (state !== "running") {
    micPaused = false;
    waitingReply = false;
    if (state === "idle" || state === "offline") {
      stopAudioRecording();
    }
  }
  const isSpeakerMode = voiceMode.value === "speaker";
  const asstName = names.assistant || t("assistant_default_name");
  const states = {
    connecting: [t("state_connecting"), t("start_chat")],
    offline: [t("state_offline"), t("start_chat")],
    idle: [t("state_idle"), selectedConversationId ? t("continue_chat") : t("start_chat")],
    starting: [t("state_starting"), t("state_starting_btn")],
    running: [
      isSpeakerMode ? t("turn_can_speak_lbl", { name: asstName }) : t("turn_barge_listening_lbl", { name: asstName }),
      t("stop_chat")
    ],
    stopping: [t("state_stopping"), t("state_stopping")],
  };
  const [status, action] = states[state];
  element("session-state").dataset.state = state;
  element("state-label").textContent = status;
  element("toggle-label").textContent = action;
  button.dataset.stop = String(state === "running");
  updateControls();
  updateMicStatus();
}

function notice(message) {
  const alert = element("alert");
  alert.textContent = message || "";
  alert.hidden = !message;
}

function showMessage(role, text, draft) {
  if (!Object.hasOwn(names, role) || typeof text !== "string" || !text) return;
  const follow = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48;
  let entry = drafts.get(role);
  if (!entry) {
    entry = document.createElement("article");
    entry.className = "bubble";
    entry.dataset.role = role;
    const label = document.createElement("span");
    label.className = "speaker";
    label.textContent = names[role];
    const body = document.createElement("div");
    body.className = "bubble-text";
    entry.append(label, body);
    messages.appendChild(entry);
    element("empty").hidden = true;
  }
  entry.lastElementChild.textContent = text;
  entry.dataset.draft = String(Boolean(draft));
  if (draft) {
    drafts.set(role, entry);
  } else {
    drafts.delete(role);
  }
  if (follow) {
    cancelAnimationFrame(scrollFrame);
    scrollFrame = requestAnimationFrame(() => {
      scroller.scrollTop = scroller.scrollHeight;
    });
  }
}

function renderConversationMessages(history) {
  currentConversationMessages = Array.isArray(history) ? [...history] : [];
  drafts.clear();
  messages.replaceChildren();
  for (const item of currentConversationMessages) {
    showMessage(item.role, item.content, false);
  }
  element("empty").hidden = currentConversationMessages.length > 0;
  scroller.scrollTop = scroller.scrollHeight;
  phase("");
}

function finishDrafts() {
  for (const draft of drafts.values()) {
    draft.dataset.draft = "false";
  }
  drafts.clear();
}

function phase(text, highlightStage) {
  element("turn-state").textContent = text;
  for (const stage of stageNames) {
    element(`${stage}-row`).dataset.pending = String(stage === highlightStage);
  }
}

function duration(ms) {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function onEvent(event) {
  if (event.kind === "endpoint") {
    waitingReply = true;
    serverGenerating = true;
    updateMicStatus();
  } else if (["error", "no_speech", "empty_reply", "interrupted"].includes(event.kind)) {
    if (tailHangoverTimer) {
      clearTimeout(tailHangoverTimer);
      tailHangoverTimer = null;
    }
    inTailHangover = false;
    waitingReply = false;
    serverGenerating = false;
    hasPlayedAudioInTurn = false;
    updateMicStatus();
  } else if (event.kind === "message" && event.role === "assistant") {
    waitingReply = false;
    updateMicStatus();
  }
  switch (event.kind) {
    case "state": setState(event.state); break;
    case "conversation":
      selectedConversationId = event.id;
      refreshConversationList();
      break;
    case "mic_paused":
      if (state === "running") {
        micPaused = event.paused === true;
        updateMicStatus();
      }
      break;
    case "devices": showDevices(event); break;
    case "devices_error":
      loadingDevices = false;
      deviceError = event.message;
      updateControls();
      break;
    case "error": notice(event.message || (event.message_key ? t(event.message_key) : "")); finishDrafts(); phase(""); break;
    case "speech_start":
      serverGenerating = false;
      hasPlayedAudioInTurn = false;
      notice();
      finishDrafts();
      for (const stage of stageNames) element(stage).textContent = "—";
      element("llm-sub").textContent = t("metric_llm_sub");
      phase(t("phase_asr_recognizing"), "asr");
      break;
    case "endpoint":
      phase(t("phase_asr_finalizing"), "asr");
      break;
    case "user_partial": showMessage("user", event.text, true); break;
    case "assistant_partial": showMessage("assistant", event.text, true); break;
    case "character_info":
      if (event.name) {
        names.assistant = (event.name === "千夏" || event.name === "mio") ? t("assistant_default_name") : event.name;
        if (charNameDisplay) charNameDisplay.textContent = names.assistant;
      }
      if (event.avatar) setAvatar(event.avatar);
      break;
    case "characters":
      if (event.characters && event.characters.length) {
        charactersData = event.characters;
        populateCharacters(charactersData);
      }
      break;
    case "message":
      showMessage(event.role, event.text, false);
      currentConversationMessages.push({ role: event.role, content: event.text });
      refreshConversationList();
      if (event.role === "user") phase(t("phase_llm_generating", { name: names.assistant }), "llm");
      if (event.role === "assistant") phase("");
      break;
    case "tts_start": phase(t("phase_tts_synthesizing", { name: names.assistant }), "tts"); break;
    case "output_complete":
      serverGenerating = false;
      checkTurnCompletion();
      break;
    case "timing":
      if (!Number.isFinite(event.milliseconds)) break;
      if (event.stage === "llm_first") {
        element("llm-sub").textContent = t("metric_first_token", { ms: duration(event.milliseconds) });
      } else if (stageNames.includes(event.stage)) {
        element(`${event.stage}-row`).dataset.pending = "false";
        element(event.stage).textContent = duration(event.milliseconds);
        if (event.stage === "tts") phase("");
      }
      break;
    case "no_speech":
      serverGenerating = false;
      drafts.get("user")?.remove();
      drafts.delete("user");
      element("empty").hidden = messages.children.length > 0;
      phase(t("phase_can_continue"));
      break;
    case "empty_reply":
      serverGenerating = false;
      phase(t("phase_no_reply"));
      break;
    case "interrupted":
      serverGenerating = false;
      stopAudioPlayback();
      finishDrafts();
      phase(t("phase_interrupted", { name: names.assistant }));
      break;
  }
}

function connect() {
  socket = new WebSocket(`ws://${wsHost}/session`);
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ action: "init" }));
    requestDevices();
  });
  socket.addEventListener("message", (event) => {
    if (event.data instanceof ArrayBuffer) {
      queueAudioChunk(event.data);
    } else {
      try {
        onEvent(JSON.parse(event.data));
      } catch (err) {
        console.error("Failed to parse event JSON", err);
      }
    }
  });
  socket.addEventListener("close", () => {
    loadingDevices = false;
    deviceListReady = false;
    setState("offline");
    setTimeout(connect, 1600);
  });
}

button.addEventListener("click", async () => {
  if (socket.readyState !== WebSocket.OPEN) return;
  const stop = state === "running";
  notice();
  if (stop) {
    setState("stopping");
    stopAudioRecording();
    socket.send(JSON.stringify({ action: "stop" }));
  } else {
    setState("starting");
    try {
      await startAudioRecording();
      socket.send(JSON.stringify({
        action: "start",
        mode: voiceMode.value,
        barge_in: voiceMode.value === "barge_in",
        speaker_mode: voiceMode.value === "speaker",
        character: charSelect ? charSelect.value : "mio",
        voice: voiceSelect ? voiceSelect.value : "chinatsu",
        tts_engine: ttsEngineSelect ? ttsEngineSelect.value : "gpt_sovits",
        conversation_id: selectedConversationId,
      }));
    } catch (err) {
      console.error("Failed to start audio recording", err);
      notice(t("mic_error", { error: err.message || err }));
      stopAudioRecording();
      setState("idle");
    }
  }
});

window.addEventListener("beforeunload", () => {
  stopAudioRecording();
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ action: "stop" }));
  }
});

refresh.addEventListener("click", requestDevices);
populateCharacters(charactersData);
updateVoiceOptions();
fetchVoicesAndCharacters();
updateMicStatus();
connect();

requestDevices();

if (langToggle) {
  langToggle.addEventListener("click", () => {
    const current = window.ovcI18n ? window.ovcI18n.locale : "zh-CN";
    const next = current.startsWith("zh") ? "en" : "zh-CN";
    if (window.ovcI18n && window.ovcI18n.setLocale) {
      window.ovcI18n.setLocale(next);
    }
  });
}

window.addEventListener("ovc:locale-changed", () => {
  const char = charactersData.find(item => item.id === (charSelect ? charSelect.value : ""));
  names.assistant = getCharacterName(char);
  if (charNameDisplay) charNameDisplay.textContent = names.assistant;
  if (charSelect && charSelect.value) {
    const prevVal = charSelect.value;
    populateCharacters(charactersData);
    charSelect.value = prevVal;
  }
  const prevVoice = voiceSelect ? voiceSelect.value : "";
  updateVoiceOptions();
  if (voiceSelect && prevVoice) {
    voiceSelect.value = prevVoice;
  }
  updateControls();
  updateMicStatus();
  refreshConversationList();
  if (currentConversationMessages.length > 0) {
    renderConversationMessages(currentConversationMessages);
  }
});
