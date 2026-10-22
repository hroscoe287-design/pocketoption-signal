// DESIRE voice test and local audio helper. Uses one stable device voice; no API key or mixed MP3 voices.
window.playDesireVoiceTest = async function () {
  try {
    if (!("speechSynthesis" in window) || !window.SpeechSynthesisUtterance) return false;
    const synth = window.speechSynthesis;
    synth.cancel();
    const chooseVoice = () => {
      const voices = synth.getVoices() || [];
      const english = voices.filter(v => /^en([_-]|$)/i.test(v.lang || ""));
      const pool = english.length ? english : voices;
      const score = v => {
        const name = String(v.name || "").toLowerCase();
        const lang = String(v.lang || "").toLowerCase();
        let value = 0;
        if (/en-us|en_us/.test(lang)) value += 50;
        if (/samantha|jenny|aria|zira|ava|serena|victoria|karen|susan|google us english|female/.test(name)) value += 35;
        if (/natural|enhanced|premium/.test(name)) value += 12;
        if (/compact|robot|male|daniel|david|alex/.test(name)) value -= 20;
        if (v.localService) value += 2;
        return value;
      };
      return pool.slice().sort((a, b) => score(b) - score(a))[0] || null;
    };
    const say = () => new Promise(resolve => {
      const utterance = new SpeechSynthesisUtterance("I am Desire. Superior signal detected. Please verify the live setup.");
      const voice = chooseVoice();
      if (voice) utterance.voice = voice;
      utterance.lang = (voice && voice.lang) || "en-US";
      utterance.rate = 0.94;
      utterance.pitch = 1.08;
      utterance.volume = 1;
      let finished = false, timer;
      const finish = () => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        resolve(true);
      };
      timer = setTimeout(finish, 7000);
      utterance.onend = finish;
      utterance.onerror = finish;
      synth.speak(utterance);
    });
    if (synth.getVoices().length) return await say();
    return await new Promise(resolve => {
      let done = false;
      const ready = async () => {
        if (done) return;
        done = true;
        if (synth.removeEventListener) synth.removeEventListener("voiceschanged", ready);
        resolve(await say());
      };
      if (synth.addEventListener) synth.addEventListener("voiceschanged", ready, { once: true });
      setTimeout(ready, 900);
    });
  } catch (error) {
    console.error("DESIRE voice playback failed", error);
    return false;
  }
};
