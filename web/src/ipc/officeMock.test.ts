import { expect, it } from "vitest";
import { mockRequest } from "./mock";

it("does not fabricate generated or verified Office files in mock mode", async () => {
  const emit = {} as Parameters<typeof mockRequest>[2];
  const created = await mockRequest("project.create", { name: "Office mock", root_path: "/mock" }, emit) as { project: { id: string } };
  expect(await mockRequest("office.list", { project_id: created.project.id }, emit)).toEqual({ deliveries: [], service_id: "mock-office-unavailable" });
  await expect(mockRequest("office.generate_xlsx", { project_id: created.project.id }, emit)).rejects.toThrow("real backend");
  await expect(mockRequest("office.get", { project_id: created.project.id, delivery_id: "off_1" }, emit)).rejects.toThrow("real backend");
  await expect(mockRequest("office.list", { project_id: "absent" }, emit)).rejects.toThrow("existing project");
});
