/**
 * useVoiceInput — SpeechRecognition-backed voice dictation for the
 * composer. Gracefully no-ops (with a toast) when the browser does
 * not expose the SpeechRecognition API.
 */
import { useCallback, useRef, useState } from "react";
import { toast } from "../layout/ErrorBoundary";

interface VoiceRecognitionResult {
  readonly [index: number]: { transcript: string };
}

interface VoiceRecognitionResultEvent {
  resultIndex: number;
  results: ArrayLike<VoiceRecognitionResult>;
}

interface VoiceRecognitionErrorEvent {
  error: string;
}

interface VoiceRecognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: VoiceRecognitionResultEvent) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: VoiceRecognitionErrorEvent) => void) | null;
  start: () => void;
  stop: () => void;
}

type VoiceRecognitionConstructor = new () => VoiceRecognition;
type VoiceWindow = Window & {
  SpeechRecognition?: VoiceRecognitionConstructor;
  webkitSpeechRecognition?: VoiceRecognitionConstructor;
};

export interface VoiceInput {
  listening: boolean;
  toggleVoice: () => void;
}

export function useVoiceInput(
  setValue: React.Dispatch<React.SetStateAction<string>>,
): VoiceInput {
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef<VoiceRecognition | null>(null);

  const toggleVoice = useCallback(() => {
    const speechWindow = window as VoiceWindow;
    const SR = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
    if (!SR) {
      toast.info("语音输入不可用", "当前浏览器不支持 SpeechRecognition API");
      return;
    }

    if (listening && recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
      setListening(false);
      return;
    }

    const recognition = new SR();
    recognition.lang = "zh-CN";
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onresult = (event: VoiceRecognitionResultEvent) => {
      let transcript = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      if (transcript) {
        setValue((v) => {
          // Append voice text to existing content
          const sep = v && !v.endsWith(" ") && !v.endsWith("\n") ? " " : "";
          return v + sep + transcript;
        });
      }
    };

    recognition.onend = () => {
      setListening(false);
      recognitionRef.current = null;
    };

    recognition.onerror = (event: VoiceRecognitionErrorEvent) => {
      setListening(false);
      recognitionRef.current = null;
      if (event.error !== "no-speech") {
        toast.error("语音识别出错", event.error);
      }
    };

    recognitionRef.current = recognition;
    recognition.start();
    setListening(true);
  }, [listening, setValue]);

  return { listening, toggleVoice };
}
