(() => {
  const translations = {
    "zh-CN": {
      history_title: "历史对话",
      new_chat: "新建",
      loading_history: "正在加载记录…",
      no_history: "还没有历史对话",
      history_unavailable: "无法读取历史记录",
      history_open_failed: "无法打开这条历史对话，请刷新后重试。",
      history_missing: "这条历史对话已不存在，请新建对话后重试。",
      new_conversation: "新对话",
      start_chat: "开始对话",
      continue_chat: "继续对话",
    },
    en: {
      history_title: "Conversation history",
      new_chat: "New",
      loading_history: "Loading history…",
      no_history: "No conversation history yet",
      history_unavailable: "Conversation history is unavailable",
      history_open_failed: "Could not open this conversation. Refresh and try again.",
      history_missing: "This conversation no longer exists. Start a new one and try again.",
      new_conversation: "New conversation",
      start_chat: "Start conversation",
      continue_chat: "Continue conversation",
    },
  };
  const locale = navigator.language?.toLowerCase().startsWith("en") ? "en" : "zh-CN";
  const dictionary = translations[locale];
  const translate = key => dictionary[key] || translations["zh-CN"][key] || key;

  for (const node of document.querySelectorAll("[data-i18n]")) {
    node.textContent = translate(node.dataset.i18n);
  }
  for (const node of document.querySelectorAll("[data-i18n-aria-label]")) {
    node.setAttribute("aria-label", translate(node.dataset.i18nAriaLabel));
  }

  window.ovcI18n = { locale, translate };
})();
