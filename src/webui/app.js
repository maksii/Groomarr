const STORAGE_KEY = "groomarr.ui.settings";
const { createApp } = Vue;

const createRuleDefaults = () => ({
  indexers_include: [],
  indexers_exclude: [],
  qualities_include: [],
  qualities_exclude: [],
  customformats_require_any: [],
  customformats_exclude: [],
  min_customformat_score: null,
  download_clients_include: [],
  download_clients_exclude: [],
  release_groups_include: [],
  release_groups_exclude: [],
  prefix: "",
  suffix: "",
  remove_patterns: [],
  replace_patterns: [],
  skip_title_patterns: [],
  validate_custom_format_score: false,
  score_validation_policy: "block",
});

const normalizeReplacePatterns = (value) => {
  if (!value) {
    return [];
  }
  if (Array.isArray(value)) {
    return value.map((item) => ({
      pattern: item.pattern || "",
      replacement: item.replacement || "",
    }));
  }
  return Object.entries(value).map(([pattern, replacement]) => ({
    pattern,
    replacement: replacement ?? "",
  }));
};

const normalizeRules = (rules) => {
  const defaults = createRuleDefaults();
  const merged = { ...defaults, ...(rules || {}) };
  merged.replace_patterns = normalizeReplacePatterns(merged.replace_patterns);
  return merged;
};

const compactList = (items) => items.map((item) => item.trim()).filter(Boolean);

const serializeReplacePatterns = (items) =>
  items
    .filter((item) => item.pattern && item.pattern.trim().length > 0)
    .reduce((acc, item) => {
      acc[item.pattern] = item.replacement ?? "";
      return acc;
    }, {});

const toRulesPayload = (rules) => ({
  ...rules,
  indexers_include: compactList(rules.indexers_include),
  indexers_exclude: compactList(rules.indexers_exclude),
  qualities_include: compactList(rules.qualities_include),
  qualities_exclude: compactList(rules.qualities_exclude),
  customformats_require_any: compactList(rules.customformats_require_any),
  customformats_exclude: compactList(rules.customformats_exclude),
  download_clients_include: compactList(rules.download_clients_include),
  download_clients_exclude: compactList(rules.download_clients_exclude),
  release_groups_include: compactList(rules.release_groups_include),
  release_groups_exclude: compactList(rules.release_groups_exclude),
  remove_patterns: compactList(rules.remove_patterns),
  skip_title_patterns: compactList(rules.skip_title_patterns),
  replace_patterns: serializeReplacePatterns(rules.replace_patterns),
  min_customformat_score:
    rules.min_customformat_score === "" || Number.isNaN(rules.min_customformat_score)
      ? null
      : rules.min_customformat_score,
});

createApp({
  data() {
    return {
      rulesPath: "",
      statusText: "Idle",
      statusClass: "",
      lastUpdated: "",
      errorMessage: "",
      isLoading: false,
      isSaving: false,
      apiKey: "",
      theme: "light",
      globalRules: createRuleDefaults(),
      trackers: [],
    };
  },
  computed: {
    themeLabel() {
      return this.theme === "dark" ? "Switch to Light" : "Switch to Dark";
    },
  },
  mounted() {
    this.restoreSettings();
    this.applyTheme();
    this.loadRules();
  },
  methods: {
    buildHeaders() {
      const headers = {
        "Content-Type": "application/json",
      };
      if (this.apiKey) {
        headers["X-API-Key"] = this.apiKey;
      }
      return headers;
    },
    addListItem(list) {
      list.push("");
    },
    removeListItem(list, index) {
      list.splice(index, 1);
    },
    addReplaceItem(list) {
      list.push({ pattern: "", replacement: "" });
    },
    removeReplaceItem(list, index) {
      list.splice(index, 1);
    },
    addTracker() {
      this.trackers.push({
        name: "",
        match: [""],
        rules: createRuleDefaults(),
      });
    },
    removeTracker(index) {
      this.trackers.splice(index, 1);
    },
    applyYamlConfig(payload) {
      const data = payload || {};
      const isHierarchical = data.global || data.trackers;
      if (isHierarchical) {
        this.globalRules = normalizeRules(data.global || {});
        this.trackers = (data.trackers || []).map((tracker) => ({
          name: tracker.name || "",
          match: tracker.match || [],
          rules: normalizeRules(tracker.rules || {}),
        }));
      } else {
        this.globalRules = normalizeRules(data);
        this.trackers = [];
      }
    },
    async loadRules() {
      this.isLoading = true;
      this.errorMessage = "";
      this.statusText = "Loading...";
      this.statusClass = "";
      try {
        const response = await fetch("/api/config/rename-rules", {
          headers: this.buildHeaders(),
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const payload = await response.json();
        this.rulesPath = payload.path || "";
        const parsed = payload.yaml ? window.jsyaml.load(payload.yaml) : {};
        this.applyYamlConfig(parsed);
        this.statusText = "Loaded";
        this.statusClass = "success";
        this.lastUpdated = new Date().toLocaleString();
      } catch (error) {
        this.statusText = "Load failed";
        this.statusClass = "error";
        this.errorMessage = error.message || "Failed to load rules.";
      } finally {
        this.isLoading = false;
      }
    },
    async saveRules() {
      this.isSaving = true;
      this.errorMessage = "";
      this.statusText = "Saving...";
      this.statusClass = "";
      try {
        const payload = {
          global: toRulesPayload(this.globalRules),
          trackers: this.trackers.map((tracker) => ({
            name: tracker.name || "tracker",
            match: compactList(tracker.match),
            rules: toRulesPayload(tracker.rules),
          })),
        };
        const yamlPayload = window.jsyaml.dump(payload, { lineWidth: 120 });
        const response = await fetch("/api/config/rename-rules", {
          method: "POST",
          headers: this.buildHeaders(),
          body: JSON.stringify({ yaml: yamlPayload }),
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
        this.statusText = "Saved & reloaded";
        this.statusClass = "success";
        this.lastUpdated = new Date().toLocaleString();
      } catch (error) {
        this.statusText = "Save failed";
        this.statusClass = "error";
        this.errorMessage = error.message || "Failed to save rules.";
      } finally {
        this.isSaving = false;
      }
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      this.applyTheme();
      this.persistSettings();
    },
    applyTheme() {
      document.documentElement.setAttribute("data-theme", this.theme);
    },
    persistSettings() {
      const settings = {
        apiKey: this.apiKey,
        theme: this.theme,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    },
    restoreSettings() {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return;
      }
      try {
        const settings = JSON.parse(raw);
        this.apiKey = settings.apiKey || "";
        this.theme = settings.theme || "light";
      } catch (error) {
        localStorage.removeItem(STORAGE_KEY);
      }
    },
  },
}).mount("#app");
