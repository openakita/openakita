window.OpenAkitaI18N = window.OpenAkitaI18N || {
  dictionaries: {},
  register: function register(name, dict) {
    this.dictionaries[name] = dict || {};
  },
  t: function t(key, fallback) {
    return fallback || key;
  },
};

