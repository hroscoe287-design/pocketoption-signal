// DESIRE voice test and local audio helper. No API key is needed for browser speech.
window.playDesireVoiceTest = async function () {
  try {
    if ("speechSynthesis" in window && window.SpeechSynthesisUtterance) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance("I am Desire. Superior signal detected. Please verify the live setup.");
      const voices = window.speechSynthesis.getVoices();
      const preferred = voices.find(v => /female|samantha|victoria|zira|aria|jenny|karen/i.test(v.name));
      if (preferred) utterance.voice = preferred;
      utterance.rate = 0.94;
      utterance.pitch = 1.12;
      utterance.volume = 1;
      window.speechSynthesis.speak(utterance);
      return true;
    }
    return false;
  } catch (error) {
    console.error("DESIRE voice playback failed", error);
    return false;
  }
};
