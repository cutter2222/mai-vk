import type { OfficeDocument, OfficeObject, OfficeSelection } from "@/lib/api/client";

/** Selection is local to one slide of one immutable document revision. */
export function selectOfficeObject(doc: OfficeDocument, obj: OfficeObject, current: OfficeSelection | null, additive: boolean): OfficeSelection | null {
  const previous = additive && current?.documentId === doc.id && current.revision === doc.revision && current.slide === obj.slide ? current.objects : [];
  const objects = previous.some((item) => item.shape_id === obj.shape_id)
    ? previous.filter((item) => item.shape_id !== obj.shape_id)
    : [...previous, { slide: obj.slide, shape_id: obj.shape_id, label: obj.label }];
  if (!objects.length) return null;
  return {
    documentId: doc.id, revision: doc.revision, slide: obj.slide, shape_id: objects[0].shape_id, objects,
    label: objects.length === 1 ? objects[0].label : `Выбрано объектов: ${objects.length} · ${objects.map((item) => item.label).join("; ")}`,
  };
}