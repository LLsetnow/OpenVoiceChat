(() => {
  const translations = {
    "zh-CN": {
      // 页面与通用
      page_title_tab: "语音对话 · OpenVoiceChat",
      character_rail_label: "角色立绘",
      avatar_alt: "千夏全身立绘",
      state_connecting: "正在连接",
      state_offline: "连接已断开",
      state_idle: "麦克风已停止",
      state_starting: "正在打开麦克风",
      state_starting_btn: "正在启动",
      state_stopping: "正在停止",
      page_title_prefix: "语音对话 · ",
      page_desc: "支持免提扬声器播放与回声抑制，无需佩戴耳机即可流畅对话。",

      // 角色与引擎
      char_select_label: "角色设定:",
      tts_engine_select_label: "语音引擎:",
      voice_select_label: "音色:",
      conv_lang_select_label: "对话语言:",
      conv_lang_auto: "自动 (跟随输入)",
      conv_lang_zh: "中文",
      conv_lang_en: "English",
      conv_lang_hint_auto: "自动根据您说话的语言回复",
      conv_lang_hint_zh: "{name}将始终使用中文回答",
      conv_lang_hint_en: "{name}将始终使用英文回答",
      tts_lang_fallback: "当前语音引擎不支持所选语言 {lang}，已自动切换为 {fallback}。",
      tts_lang_unsupported: "当前语音引擎可能对所选语言支持有限。",
      turn_failed: "本轮对话失败，请再说一次。",
      session_occupied: "对话正在另一个页面使用，请先在那里停止。",
      missing_configuration: "缺少环境变量：{missing}。请在启动终端中导出，或设在项目的 .env 文件中。",
      session_runtime_error: "运行语音对话遇到错误，请刷新后重试。",
      devices_scan_failed: "无法识别音频设备，请检查设备连接后刷新。",
      char_mio_desc: "千夏 (表妹恋人)",
      char_example_desc: "千夏 (标准助手)",
      engine_sovits: "AiServer GPT-SoVITS",
      engine_cosyvoice: "阿里云 CosyVoice",

      // 音色名称
      voice_chinatsu: "千夏 (chinatsu)",
      voice_ling: "铃 (ling)",
      voice_zhe: "哲 (zhe)",
      voice_nangongyu: "南宫羽 (nangongyu)",
      voice_remielle: "雷米埃尔 (remielle)",
      voice_longxiaochun: "龙小淳 (知性积极)",
      voice_longyue: "龙悦 (温暖磁性)",
      voice_longhua: "龙华 (元气甜美)",
      voice_longfeifei: "龙菲菲 (甜美娇气)",
      voice_longwan: "龙婉 (细腻柔声)",

      // 按钮与控制
      start_chat: "开始对话",
      continue_chat: "继续对话",
      stop_chat: "停止对话",
      refresh_devices: "刷新设备",
      refresh_identifying: "正在识别…",
      identifying_devices: "正在识别设备",
      lang_switch_label: "English",
      lang_toggle_aria: "切换为英文",

      // 音频设备与模式
      audio_devices_label: "音频设备",
      audio_input_label: "麦克风",
      audio_output_label: "声音输出",
      voice_mode_label: "对话模式",
      mode_speaker: "扬声器防回声（推荐免提 · 绝不误断）",
      mode_barge_in: "智能免提打断（千夏说话时可开口打断）",
      speaker_mode_hint: "千夏播放时自动暂停麦克风，播完秒开，彻底杜绝回声打断",
      barge_mode_hint: "说话时支持随时开口打断，播完智能吸收房间尾音，彻底消除尾音误识别",
      speaker_mode_title: "扬声器防回声模式：{name}播放及尾音期间自动暂停麦克风，彻底杜绝回声打断与尾音误触发。",
      barge_mode_title: "智能免提打断模式：说话时支持随时开口打断，播完智能吸收房间尾音，彻底消除尾音误识别。",
      speaker_select_hint: "{name}播放及尾音期间自动暂停麦克风，彻底杜绝回声打断与尾音误触发",
      barge_select_hint: "说话时支持随时开口打断，播完智能吸收房间尾音，消除尾音误识别",

      // 设备提示
      device_note_running: "停止对话后可以更换设备或模式。",
      device_note_invalid: "所选设备不可用。请选择其他设备，或连接后刷新。",
      system_default: "系统默认",
      system_default_named: "系统默认 · {name}",
      default_microphone: "默认麦克风",
      microphone_indexed: "麦克风 {index}",
      default_speaker: "默认扬声器",
      speaker_indexed: "扬声器 {index}",

      // 历史记录
      history_title: "历史对话",
      new_chat: "新建",
      loading_history: "正在加载记录…",
      no_history: "还没有历史对话",
      history_unavailable: "无法读取历史记录",
      history_open_failed: "无法打开这条历史对话，请刷新后重试。",
      history_missing: "这条历史对话已不存在，请新建对话后重试。",
      char_deleted_fallback: "原角色配置已不存在，已回退至默认角色：{name}。",
      legacy_settings_fallback: "未找到历史语音配置，已采用角色默认语音设置。",
      new_conversation: "新对话",
      db_migration_failed: "数据库迁移失败，请检查写入权限后运行 python -m openvoicechat.migrate 手动恢复。",
      db_migration_recovery: "请运行 python -m openvoicechat.migrate 恢复数据库。",

      // 对话面板
      dialogue_title: "对话记录",
      dialogue_subtitles_label: "对话字幕",
      empty_title: "还没有对话",
      empty_hint: "点击开始对话，说一句试试。",
      mic_hint_default: "免提监听中，{name}说话时可随时开口打断。",
      user_name: "你",
      assistant_default_name: "千夏",

     // 文本输入
     text_input_placeholder: "输入文字向{name}提问…（Enter 发送）",
     text_input_aria: "文字输入框",
     send_text_button: "发送",
     send_text_title: "发送文字（Enter）",
     metric_text_input: "文字输入",

      // 耗时监控
      timing_title: "本轮耗时",
      metric_asr: "语音识别",
      metric_asr_hint: "判停到最终转写",
      metric_llm: "生成首段",
      metric_llm_sub: "DeepSeek Flash",
      metric_tts: "合成首段",
      metric_tts_sub: "GPT-SoVITS · {name}",
      metric_first_token: "首 token {ms}",

      // 实时交互状态
      turn_can_speak_lbl: "可以说话（麦克风已开启）",
      turn_can_speak_hnt: "请开口说话，说完整句话后{name}会自动回答。",
      turn_speaking_paused_lbl: "{name}正在说话（麦克风已自动暂停）",
      turn_speaking_paused_hnt: "{name}正在回答中，请听完后再说话（播完会自动开麦）。",
      turn_absorbing_lbl: "{name}播完（回声吸收中…）",
      turn_absorbing_hnt: "正在消除房间尾音回响，马上开麦…",
      turn_thinking_lbl: "{name}正在思考回复",
      turn_thinking_hnt: "{name}正在准备回复，请稍候。",

      turn_barge_speaking_lbl: "{name}正在说话（可随时开口打断）",
      turn_barge_speaking_hnt: "智能回声过滤已开启，你只要开口说话即可随时打断{name}。",
      turn_barge_continuing_lbl: "{name}正在继续回答…",
      turn_barge_continuing_hnt: "{name}正在组织下一句话…",
      turn_barge_absorbing_lbl: "{name}播完（回声吸收中…）",
      turn_barge_absorbing_hnt: "智能尾音消除中，马上恢复监听…",
      turn_barge_thinking_lbl: "{name}正在准备回复",
      turn_barge_thinking_hnt: "{name}正在思考回复。",
      turn_barge_listening_lbl: "免提监听中（随时说话）",
      turn_barge_listening_hnt: "免提监听中，你可以随时说话。",

      phase_asr_recognizing: "正在识别",
      phase_asr_finalizing: "正在获取最终转写",
      phase_llm_generating: "{name}正在生成回复",
      phase_tts_synthesizing: "正在合成{name}的语音",
      phase_can_continue: "可以继续说话",
      phase_no_reply: "这轮没有生成回复，可以继续说话",
      phase_interrupted: "已打断，{name}正在聆听",
      retry_read: "重试朗读",
      retry_read_aria: "重新朗读此条回复",
      tts_error_notice: "语音合成超时或失败，已保留文字回复，可点击重试朗读。",
      phase_tts_error: "语音合成失败，已保留文字",
      mic_error: "麦克风启动失败：{error}。请检查浏览器麦克风权限。"
    },
    en: {
      // Page & general
      page_title_tab: "Voice Chat · OpenVoiceChat",
      character_rail_label: "Character Artwork",
      avatar_alt: "Chinatsu Full-body Portrait",
      state_connecting: "Connecting",
      state_offline: "Disconnected",
      state_idle: "Microphone stopped",
      state_starting: "Opening microphone",
      state_starting_btn: "Starting",
      state_stopping: "Stopping",
      page_title_prefix: "Voice Chat · ",
      page_desc: "Hands-free speaker playback with echo suppression for smooth conversation without headphones.",

      // Character & engines
      char_select_label: "Character:",
      tts_engine_select_label: "TTS Engine:",
      voice_select_label: "Voice:",
      conv_lang_select_label: "Voice Lang:",
      conv_lang_auto: "Auto (Follow input)",
      conv_lang_zh: "Chinese",
      conv_lang_en: "English",
      conv_lang_hint_auto: "Automatically replies in the language you speak",
      conv_lang_hint_zh: "{name} will always reply in Chinese",
      conv_lang_hint_en: "{name} will always reply in English",
      tts_lang_fallback: "Current TTS engine does not support language '{lang}', switched to '{fallback}'.",
      tts_lang_unsupported: "Current TTS engine may have limited support for the selected language.",
      turn_failed: "Conversation turn failed, please try speaking again.",
      session_occupied: "Conversation is currently in use in another tab. Please stop it there first.",
      missing_configuration: "Missing environment variables: {missing}. Please export them or set in .env.",
      session_runtime_error: "Voice session encountered an error. Please refresh and try again.",
      devices_scan_failed: "Failed to detect audio devices. Please check connections and refresh.",
      char_mio_desc: "Chinatsu (Cousin & Sweetheart)",
      char_example_desc: "Chinatsu (Default Assistant)",
      engine_sovits: "AiServer GPT-SoVITS",
      engine_cosyvoice: "Alibaba Cloud CosyVoice",

      // Voice names
      voice_chinatsu: "Chinatsu (chinatsu)",
      voice_ling: "Ling (ling)",
      voice_zhe: "Zhe (zhe)",
      voice_nangongyu: "Nangong Yu (nangongyu)",
      voice_remielle: "Remielle (remielle)",
      voice_longxiaochun: "Long Xiaochun (Intellectual)",
      voice_longyue: "Long Yue (Warm Magnetic)",
      voice_longhua: "Long Hua (Energetic Sweet)",
      voice_longfeifei: "Long Feifei (Sweet Charming)",
      voice_longwan: "Long Wan (Gentle Soft)",

      // Buttons & controls
      start_chat: "Start conversation",
      continue_chat: "Continue conversation",
      stop_chat: "Stop conversation",
      refresh_devices: "Refresh Devices",
      refresh_identifying: "Detecting…",
      identifying_devices: "Detecting devices…",
      lang_switch_label: "中文",
      lang_toggle_aria: "Switch to Chinese",

      // Audio devices & modes
      audio_devices_label: "Audio Devices",
      audio_input_label: "Microphone",
      audio_output_label: "Audio Output",
      voice_mode_label: "Conversation Mode",
      mode_speaker: "Speaker Echo Prevention (Recommended · No interruption)",
      mode_barge_in: "Smart Barge-in (Interrupt anytime while speaking)",
      speaker_mode_hint: "Microphone automatically pauses during playback and resumes instantly to prevent echo interruptions.",
      barge_mode_hint: "Interrupt anytime while speaking, with smart room reverb suppression.",
      speaker_mode_title: "Speaker Echo Prevention: Microphone pauses while {name} is speaking to prevent echo interruptions.",
      barge_mode_title: "Smart Barge-in: Speak anytime to interrupt playback, with intelligent reverb suppression.",
      speaker_select_hint: "Microphone pauses while {name} is speaking to prevent echo interruptions",
      barge_select_hint: "Speak anytime to interrupt playback, with intelligent reverb suppression",

      // Device notes
      device_note_running: "You can change devices or mode after stopping the conversation.",
      device_note_invalid: "Selected device is unavailable. Choose another or refresh after connecting.",
      system_default: "System Default",
      system_default_named: "System Default · {name}",
      default_microphone: "Default Microphone",
      microphone_indexed: "Microphone {index}",
      default_speaker: "Default Speaker",
      speaker_indexed: "Speaker {index}",

      // History
      history_title: "Conversation history",
      new_chat: "New",
      loading_history: "Loading history…",
      no_history: "No conversation history yet",
      history_unavailable: "Conversation history is unavailable",
      history_open_failed: "Could not open this conversation. Refresh and try again.",
      history_missing: "This conversation no longer exists. Start a new one and try again.",
      char_deleted_fallback: "The original character profile no longer exists. Reverted to default character: {name}.",
      legacy_settings_fallback: "No legacy voice settings found. Character default voice settings have been applied.",
      new_conversation: "New conversation",
      db_migration_failed: "Database migration failed. Please check write permissions and run python -m openvoicechat.migrate to recover.",
      db_migration_recovery: "Please run python -m openvoicechat.migrate to restore the database.",

      // Dialogue panel
      dialogue_title: "Conversation",
      dialogue_subtitles_label: "Conversation subtitles",
      empty_title: "No conversation yet",
      empty_hint: "Click start conversation and try saying something.",
      mic_hint_default: "Hands-free listening active. You can speak anytime to interrupt {name}.",
      user_name: "You",
      assistant_default_name: "Chinatsu",

     // Text input
     text_input_placeholder: "Type a message to {name}... (Enter to send)",
     text_input_aria: "Text input box",
     send_text_button: "Send",
     send_text_title: "Send message (Enter)",
     metric_text_input: "Text input",

      // Timings
      timing_title: "Turn Latency",
      metric_asr: "Speech Recognition",
      metric_asr_hint: "Endpoint to final transcript",
      metric_llm: "First Token",
      metric_llm_sub: "DeepSeek Flash",
      metric_tts: "First Audio Chunk",
      metric_tts_sub: "GPT-SoVITS · {name}",
      metric_first_token: "First token {ms}",

      // Live interaction states
      turn_can_speak_lbl: "Ready to speak (mic active)",
      turn_can_speak_hnt: "Please speak; {name} will reply automatically after a full sentence.",
      turn_speaking_paused_lbl: "{name} is speaking (mic paused)",
      turn_speaking_paused_hnt: "{name} is replying, please listen (mic resumes automatically).",
      turn_absorbing_lbl: "{name} finished (absorbing echo…)",
      turn_absorbing_hnt: "Suppressing room reverb, reopening mic shortly…",
      turn_thinking_lbl: "{name} is thinking",
      turn_thinking_hnt: "{name} is preparing a reply, please wait.",

      turn_barge_speaking_lbl: "{name} is speaking (interrupt anytime)",
      turn_barge_speaking_hnt: "Smart echo filter active, speak anytime to interrupt {name}.",
      turn_barge_continuing_lbl: "{name} is continuing to reply…",
      turn_barge_continuing_hnt: "{name} is preparing the next sentence…",
      turn_barge_absorbing_lbl: "{name} finished (absorbing echo…)",
      turn_barge_absorbing_hnt: "Reverb suppression active, resuming listening shortly…",
      turn_barge_thinking_lbl: "{name} is preparing reply",
      turn_barge_thinking_hnt: "{name} is thinking.",
      turn_barge_listening_lbl: "Hands-free listening (speak anytime)",
      turn_barge_listening_hnt: "Hands-free listening active, speak anytime.",

      phase_asr_recognizing: "Recognizing speech",
      phase_asr_finalizing: "Finalizing transcript",
      phase_llm_generating: "{name} is generating reply",
      phase_tts_synthesizing: "Synthesizing {name}'s voice",
      phase_can_continue: "Ready for more speech",
      phase_no_reply: "No reply generated this turn, please speak again",
      phase_interrupted: "Interrupted, {name} is listening",
      retry_read: "Retry Voice",
      retry_read_aria: "Retry reading this reply",
      tts_error_notice: "Voice synthesis timed out or failed; text reply preserved, click to retry reading.",
      phase_tts_error: "Voice synthesis failed; text preserved",
      mic_error: "Microphone failed to start: {error}. Please check browser microphone permissions."
    },
  };

  const userLang = (navigator.language || navigator.userLanguage || "").toLowerCase();
  const urlLang = new URLSearchParams(window.location.search).get("lang");
  let savedLang = null;
  try {
    savedLang = localStorage.getItem("openvoicechat.locale");
  } catch {}

  let locale = "zh-CN";
  if (urlLang === "en" || urlLang === "zh-cn" || urlLang === "zh") {
    locale = urlLang.startsWith("zh") ? "zh-CN" : "en";
  } else if (savedLang === "en" || savedLang === "zh-CN") {
    locale = savedLang;
  } else {
    locale = userLang.startsWith("zh") ? "zh-CN" : "en";
  }

  const translate = (key, params = {}) => {
    const dict = translations[locale] || translations["zh-CN"];
    let text = dict[key] || translations["zh-CN"][key] || key;
    const mergedParams = {
      name: dict["assistant_default_name"] || "千夏",
      ...(params && typeof params === "object" ? params : {}),
    };
    for (const [k, v] of Object.entries(mergedParams)) {
      text = text.replace(new RegExp(`\\{${k}\\}`, "g"), v);
    }
    return text;
  };

  function applyTranslations(root = document) {
    document.documentElement.lang = locale;
    document.title = translate("page_title_tab");

    for (const node of root.querySelectorAll("[data-i18n]")) {
      node.textContent = translate(node.dataset.i18n);
    }
    for (const node of root.querySelectorAll("[data-i18n-aria-label]")) {
      node.setAttribute("aria-label", translate(node.dataset.i18nAriaLabel));
    }
    for (const node of root.querySelectorAll("[data-i18n-alt]")) {
      node.setAttribute("alt", translate(node.dataset.i18nAlt));
    }
    for (const node of root.querySelectorAll("[data-i18n-title]")) {
      node.setAttribute("title", translate(node.dataset.i18nTitle));
    }
   for (const node of root.querySelectorAll("[data-i18n-placeholder]")) {
     node.setAttribute("placeholder", translate(node.dataset.i18nPlaceholder));
   }
  }

  function setLocale(nextLocale) {
    if (nextLocale !== "en" && nextLocale !== "zh-CN") return;
    locale = nextLocale;
    try {
      localStorage.setItem("openvoicechat.locale", locale);
    } catch {}
    applyTranslations();
    window.dispatchEvent(new CustomEvent("ovc:locale-changed", { detail: { locale } }));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => applyTranslations());
  } else {
    applyTranslations();
  }

  window.ovcI18n = {
    get locale() {
      return locale;
    },
    translate,
    t: translate,
    applyTranslations,
    setLocale,
  };
})();
