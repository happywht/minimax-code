/**
 * useWebhookForm — create-form state + validation for the Webhooks tab.
 */
import { useState } from "react";
import { useWebhookStore } from "../../../stores";
import { strings } from "../../../ui/strings";
import { compose, minLength, required } from "../../../lib/validators";

const nameValidator = compose(
  required(strings.settings.webhooks.fieldName),
  minLength(2, strings.settings.webhooks.fieldName),
);

export function useWebhookForm() {
  const create = useWebhookStore((s) => s.create);

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newSource, setNewSource] = useState<"github" | "gitee" | "custom">("github");
  const [newAction, setNewAction] = useState<"code-review" | "send-message">("send-message");
  const [createError, setCreateError] = useState("");

  const toggleForm = () => setShowCreate((v) => !v);
  const closeForm = () => setShowCreate(false);

  const setName = (value: string) => {
    setNewName(value);
    setCreateError("");
  };

  const handleCreate = async () => {
    const err = nameValidator(newName);
    if (err) {
      setCreateError(err);
      return;
    }
    setCreateError("");
    await create({ name: newName.trim(), source: newSource, action_type: newAction });
    setNewName("");
    setShowCreate(false);
  };

  return {
    showCreate,
    newName,
    newSource,
    newAction,
    createError,
    toggleForm,
    closeForm,
    setName,
    setNewSource,
    setNewAction,
    handleCreate,
  };
}

export type WebhookFormState = ReturnType<typeof useWebhookForm>;
