/**
 * McpServerForm — the add/edit form for MCP server configurations.
 *
 * Extracted from McpServersTab so both "add a server" and "edit an
 * existing server" share one validated form. The form owns no IPC:
 * it validates the raw string fields (command / JSON env / scopes),
 * converts them to the wire shape, and hands the result to the parent
 * via ``onSubmit``.
 */
import { useMemo, useState } from "react";
import { Button, Input } from "../../ui";
import { toast } from "../layout/ErrorBoundary";
import type { McpServer, McpTransport } from "../../types/ipc";
import { strings } from "../../ui/strings";
import { Field, Select } from "./fields";

function parseCommand(value: string): string[] {
  return value.trim().split(/\s+/).filter(Boolean);
}

function parseJsonObject(
  value: string,
): { ok: true; value: Record<string, string> } | { ok: false; error: string } {
  if (!value.trim()) return { ok: true, value: {} };
  try {
    const parsed = JSON.parse(value) as unknown;
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, error: strings.settings.mcp.envNotObject };
    }
    if (!Object.entries(parsed as Record<string, unknown>).every(([, v]) => typeof v === "string")) {
      return { ok: false, error: strings.settings.mcp.envValuesNotStrings };
    }
    return { ok: true, value: parsed as Record<string, string> };
  } catch {
    return { ok: false, error: strings.settings.mcp.invalidJson };
  }
}

function parseScopes(value: string): string[] {
  if (!value.trim()) return [];
  try {
    const parsed = JSON.parse(value) as unknown;
    if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) {
      return parsed;
    }
  } catch {
    // fall through to comma-separated
  }
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Raw string form values the inputs bind to. */
export interface McpServerFormValues {
  name: string;
  transport: McpTransport;
  command: string;
  env: string;
  url: string;
  bearerToken: string;
  headers: string;
  oauthClientId: string;
  oauthClientSecret: string;
  oauthScopes: string;
  oauthCallbackPort: string;
}

/** Wire-shaped options handed to addMcpServer / updateMcpServer. */
export interface McpServerFormOptions {
  name: string;
  transport: McpTransport;
  command?: string[];
  url?: string;
  env?: Record<string, string>;
  bearer_token?: string;
  headers?: Record<string, string>;
  oauth_client_id?: string;
  oauth_client_secret?: string;
  oauth_scopes?: string[];
  oauth_callback_port?: number;
}

/** Prefill the form from an existing server configuration. */
export function serverToFormValues(server: McpServer): McpServerFormValues {
  return {
    name: server.name,
    transport: server.transport,
    command: server.command?.join(" ") ?? "",
    env: server.env ? JSON.stringify(server.env) : "",
    url: server.url ?? "",
    bearerToken: server.bearer_token ?? "",
    headers: server.headers ? JSON.stringify(server.headers) : "",
    oauthClientId: server.oauth_client_id ?? "",
    oauthClientSecret: server.oauth_client_secret ?? "",
    oauthScopes: server.oauth_scopes?.join(", ") ?? "",
    oauthCallbackPort: server.oauth_callback_port != null ? String(server.oauth_callback_port) : "",
  };
}

const EMPTY_VALUES: McpServerFormValues = {
  name: "",
  transport: "stdio",
  command: "",
  env: "",
  url: "",
  bearerToken: "",
  headers: "",
  oauthClientId: "",
  oauthClientSecret: "",
  oauthScopes: "",
  oauthCallbackPort: "",
};

export interface McpServerFormProps {
  /** "edit" only changes the submit label; behaviour is identical. */
  mode: "add" | "edit";
  initial?: Partial<McpServerFormValues>;
  onSubmit: (opts: McpServerFormOptions) => void | Promise<void>;
  onCancel: () => void;
  /** Prefix for data-testid / input ids — keeps add and edit ids distinct. */
  idPrefix?: string;
}

export function McpServerForm({
  mode,
  initial,
  onSubmit,
  onCancel,
  idPrefix = "settings-mcp",
}: McpServerFormProps): JSX.Element {
  const [values, setValues] = useState<McpServerFormValues>({ ...EMPTY_VALUES, ...initial });
  const set = <K extends keyof McpServerFormValues>(key: K, value: McpServerFormValues[K]) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  const formErrors = useMemo(() => {
    const errors: string[] = [];
    if (!values.name.trim()) errors.push(strings.settings.mcp.nameRequired);
    if (values.transport === "stdio" && parseCommand(values.command).length === 0) {
      errors.push(strings.settings.mcp.commandRequired);
    }
    if (values.transport === "sse" && !values.url.trim()) {
      errors.push(strings.settings.mcp.urlRequired);
    }
    const envParsed = parseJsonObject(values.env);
    if (!envParsed.ok) errors.push(strings.settings.mcp.envError(envParsed.error));
    const headersParsed = parseJsonObject(values.headers);
    if (!headersParsed.ok) errors.push(strings.settings.mcp.headersError(headersParsed.error));
    return errors;
  }, [values]);

  const handleSubmit = () => {
    if (formErrors.length > 0) {
      toast.error(strings.settings.mcp.invalidInput, formErrors.join(" "));
      return;
    }
    const envParsed = parseJsonObject(values.env);
    const headersParsed = parseJsonObject(values.headers);
    if (!envParsed.ok || !headersParsed.ok) return;
    const envObj = envParsed.value;
    const headersObj = headersParsed.value;
    const scopes = parseScopes(values.oauthScopes);
    const callbackPort = values.oauthCallbackPort ? parseInt(values.oauthCallbackPort, 10) : undefined;
    if (values.oauthCallbackPort && Number.isNaN(callbackPort)) {
      toast.error(strings.settings.mcp.invalidInput, strings.settings.mcp.portInvalid);
      return;
    }
    void onSubmit({
      name: values.name.trim(),
      transport: values.transport,
      command: values.transport === "stdio" ? parseCommand(values.command) : undefined,
      url: values.transport === "sse" ? values.url.trim() : undefined,
      env: Object.keys(envObj).length > 0 ? envObj : undefined,
      bearer_token: values.bearerToken.trim() || undefined,
      headers: Object.keys(headersObj).length > 0 ? headersObj : undefined,
      oauth_client_id: values.oauthClientId.trim() || undefined,
      oauth_client_secret: values.oauthClientSecret.trim() || undefined,
      oauth_scopes: scopes.length > 0 ? scopes : undefined,
      oauth_callback_port: callbackPort,
    });
  };

  const fieldId = (suffix: string) => `${idPrefix}-${suffix}`;

  return (
    <div className="space-y-3 rounded-md border border-line bg-surface-1 p-3" data-testid={`${idPrefix}-form`}>
      <Field label={strings.settings.mcp.fieldName} htmlFor={fieldId("name")}>
        <Input
          id={fieldId("name")}
          placeholder={strings.settings.mcp.placeholderName}
          value={values.name}
          onChange={(e) => set("name", e.target.value)}
          data-testid={fieldId("name")}
        />
      </Field>

      <Field label={strings.settings.mcp.fieldTransport} htmlFor={fieldId("transport")}>
        <Select
          id={fieldId("transport")}
          value={values.transport}
          onChange={(e) => set("transport", e.target.value as McpTransport)}
          data-testid={fieldId("transport")}
        >
          <option value="stdio">{strings.settings.mcp.transportStdio}</option>
          <option value="sse">{strings.settings.mcp.transportSse}</option>
        </Select>
      </Field>

      {values.transport === "stdio" ? (
        <>
          <Field
            label={strings.settings.mcp.fieldCommand}
            htmlFor={fieldId("command")}
            hint={strings.settings.mcp.commandHint}
          >
            <Input
              id={fieldId("command")}
              placeholder="npx @modelcontextprotocol/server-filesystem ."
              value={values.command}
              onChange={(e) => set("command", e.target.value)}
              data-testid={fieldId("command")}
            />
          </Field>
          <Field
            label={strings.settings.mcp.fieldEnv}
            htmlFor={fieldId("env")}
            hint={strings.settings.mcp.envHint}
          >
            <Input
              id={fieldId("env")}
              placeholder='{"KEY":"value"}'
              value={values.env}
              onChange={(e) => set("env", e.target.value)}
              data-testid={fieldId("env")}
            />
          </Field>
        </>
      ) : (
        <>
          <Field label={strings.settings.mcp.fieldUrl} htmlFor={fieldId("url")}>
            <Input
              id={fieldId("url")}
              placeholder="http://localhost:3001/sse"
              value={values.url}
              onChange={(e) => set("url", e.target.value)}
              data-testid={fieldId("url")}
            />
          </Field>
          <Field label={strings.settings.mcp.fieldToken} htmlFor={fieldId("bearer")}>
            <Input
              id={fieldId("bearer")}
              type="password"
              placeholder={strings.settings.mcp.tokenHint}
              value={values.bearerToken}
              onChange={(e) => set("bearerToken", e.target.value)}
              data-testid={fieldId("bearer")}
            />
          </Field>
          <Field
            label={strings.settings.mcp.fieldHeaders}
            htmlFor={fieldId("headers")}
            hint={strings.settings.mcp.headersHint}
          >
            <Input
              id={fieldId("headers")}
              placeholder='{"X-Custom":"value"}'
              value={values.headers}
              onChange={(e) => set("headers", e.target.value)}
              data-testid={fieldId("headers")}
            />
          </Field>
        </>
      )}

      <div className="space-y-3 rounded-md border border-line bg-surface-0 p-3">
        <p className="text-[11px] font-medium text-ink-1">{strings.settings.mcp.oauthTitle}</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={strings.settings.mcp.clientId} htmlFor={fieldId("oauth-id")}>
            <Input
              id={fieldId("oauth-id")}
              value={values.oauthClientId}
              onChange={(e) => set("oauthClientId", e.target.value)}
              data-testid={fieldId("oauth-id")}
            />
          </Field>
          <Field label={strings.settings.mcp.clientSecret} htmlFor={fieldId("oauth-secret")}>
            <Input
              id={fieldId("oauth-secret")}
              type="password"
              value={values.oauthClientSecret}
              onChange={(e) => set("oauthClientSecret", e.target.value)}
              data-testid={fieldId("oauth-secret")}
            />
          </Field>
        </div>
        <Field
          label={strings.settings.mcp.fieldScopes}
          htmlFor={fieldId("oauth-scopes")}
          hint={strings.settings.mcp.scopesHint}
        >
          <Input
            id={fieldId("oauth-scopes")}
            placeholder="read,write"
            value={values.oauthScopes}
            onChange={(e) => set("oauthScopes", e.target.value)}
            data-testid={fieldId("oauth-scopes")}
          />
        </Field>
        <Field label={strings.settings.mcp.callbackPort} htmlFor={fieldId("oauth-port")}>
          <Input
            id={fieldId("oauth-port")}
            type="number"
            placeholder="8765"
            value={values.oauthCallbackPort}
            onChange={(e) => set("oauthCallbackPort", e.target.value)}
            data-testid={fieldId("oauth-port")}
          />
        </Field>
      </div>

      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel} data-testid={`${idPrefix}-cancel`}>
          {strings.settings.mcp.cancel}
        </Button>
        <Button
          size="sm"
          variant="primary"
          onClick={handleSubmit}
          data-testid={`${idPrefix}-save`}
        >
          {mode === "add" ? strings.settings.mcp.save : strings.settings.mcp.editServer}
        </Button>
      </div>
    </div>
  );
}
