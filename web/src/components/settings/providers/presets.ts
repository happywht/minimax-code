/**
 * PROVIDER_PRESETS — quick-fill templates for common LLM providers.
 */
export interface ProviderPreset {
  label: string;
  name: string;
  protocol: "anthropic" | "openai";
  base_url: string;
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  { label: "OpenAI", name: "OpenAI", protocol: "openai", base_url: "https://api.openai.com/v1" },
  { label: "智谱 GLM", name: "智谱 GLM", protocol: "openai", base_url: "https://open.bigmodel.cn/api/paas/v4" },
  { label: "DeepSeek", name: "DeepSeek", protocol: "openai", base_url: "https://api.deepseek.com/v1" },
  { label: "Moonshot", name: "Moonshot", protocol: "openai", base_url: "https://api.moonshot.cn/v1" },
  { label: "本地 Ollama", name: "Ollama", protocol: "openai", base_url: "http://localhost:11434/v1" },
];
