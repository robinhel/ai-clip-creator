"use client";

import { useState } from "react";
import { Upload } from "lucide-react";
import { ACCEPT } from "@/lib/uploads";
import { cn } from "@/lib/utils";

/** Dra in eller klicka för att välja, flera filer. rejected: texten om filer som inte är videor. */
export function DropZone({ onFiles, rejected }: { onFiles: (files: FileList | null) => void; rejected: string | null }) {
  const [dragging, setDragging] = useState(false);
  return (
    <div>
      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          onFiles(e.dataTransfer.files);
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center text-sm",
          dragging ? "border-brand bg-brand/10" : "border-line hover:border-muted-foreground"
        )}
      >
        <Upload className="size-6 text-muted-foreground" aria-hidden />
        <span className="font-medium">Dra in videor här, eller klicka för att välja</span>
        <span className="text-xs text-muted-foreground">mp4, mov, m4v, webm eller mkv. Videon laddas upp direkt.</span>
        <input
          type="file"
          accept={ACCEPT}
          multiple
          className="sr-only"
          onChange={(e) => {
            onFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </label>
      {rejected && (
        <p role="alert" className="mt-2 text-xs break-words text-[#fca5a5]">
          {rejected}
        </p>
      )}
    </div>
  );
}
