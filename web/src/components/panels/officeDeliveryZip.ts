// Minimal ZIP (stored entries, UTF-8 names). Fixed internal names avoid path traversal.
function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export function officeDeliveryZip(workbook: ArrayBuffer, receipt: string): Blob {
  const encoder = new TextEncoder();
  const entries = [
    { name: "workbook.xlsx", bytes: new Uint8Array(workbook) },
    { name: "office-receipt.json", bytes: encoder.encode(receipt) },
    { name: "README.txt", bytes: encoder.encode("workbook.xlsx 是所选成果的原始字节。office-receipt.json 包含成果与附件身份、校验范围及已保存的处理说明。回执不是数字签名，不证明来源、业务内容或版式正确；接收者应自行核对。说明可能包含敏感信息。\n") },
  ];
  const locals: ArrayBuffer[] = [], centrals: ArrayBuffer[] = [];
  let offset = 0;
  for (const entry of entries) {
    const name = encoder.encode(entry.name), crc = crc32(entry.bytes), size = entry.bytes.length;
    const local = new ArrayBuffer(30 + name.length), l = new DataView(local);
    l.setUint32(0, 0x04034b50, true); l.setUint16(4, 20, true); l.setUint16(6, 0x0800, true);
    l.setUint16(8, 0, true); l.setUint16(10, 0, true); l.setUint16(12, 0x21, true);
    l.setUint32(14, crc, true); l.setUint32(18, size, true); l.setUint32(22, size, true); l.setUint16(26, name.length, true);
    new Uint8Array(local, 30).set(name);
    const central = new ArrayBuffer(46 + name.length), c = new DataView(central);
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
    c.setUint16(10, 0, true); c.setUint16(12, 0, true); c.setUint16(14, 0x21, true);
    c.setUint32(16, crc, true); c.setUint32(20, size, true); c.setUint32(24, size, true); c.setUint16(28, name.length, true);
    c.setUint32(42, offset, true); new Uint8Array(central, 46).set(name);
    locals.push(local, new Uint8Array(entry.bytes).buffer as ArrayBuffer); centrals.push(central);
    offset += local.byteLength + size;
  }
  const centralSize = centrals.reduce((total, part) => total + part.byteLength, 0);
  const end = new ArrayBuffer(22), e = new DataView(end);
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, entries.length, true); e.setUint16(10, entries.length, true);
  e.setUint32(12, centralSize, true); e.setUint32(16, offset, true);
  return new Blob([...locals, ...centrals, end], { type: "application/zip" });
}
