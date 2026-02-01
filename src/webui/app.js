const STORAGE_KEY = "groomarr.ui.settings";
const { createApp } = Vue;

createApp({
  data() {
    return {
      rulesYaml: "",
      rulesPath: "",
      statusText: "Idle",
      statusClass: "",
      lastUpdated: "",
      errorMessage: "",
      isLoading: false,
      isSaving: false,
      apiKey: "",
      theme: "light",
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
        this.rulesYaml = payload.yaml || "";
        this.rulesPath = payload.path || "";
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
        const response = await fetch("/api/config/rename-rules", {
          method: "POST",
          headers: this.buildHeaders(),
          body: JSON.stringify({ yaml: this.rulesYaml }),
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const payload = await response.json();
        this.rulesYaml = payload.yaml || this.rulesYaml;
        this.rulesPath = payload.path || this.rulesPath;
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
