import { useState } from "react";

interface TooltipProps {
  text: string;
}

export default function Tooltip({ text }: TooltipProps) {
  const [show, setShow] = useState(false);

  return (
    <span
      style={{
        position: "relative",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 16,
        height: 16,
        borderRadius: "50%",
        background: "#e3f2fd",
        color: "#1976d2",
        fontSize: 11,
        fontWeight: "bold",
        cursor: "pointer",
        marginLeft: 6,
        userSelect: "none",
      }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      onClick={() => setShow(!show)}
      title={text}
    >
      🛈
      {show && (
        <span
          style={{
            position: "absolute",
            bottom: "125%",
            left: "50%",
            transform: "translateX(-50%)",
            background: "#333",
            color: "#fff",
            padding: "6px 10px",
            borderRadius: 4,
            fontSize: 12,
            whiteSpace: "normal",
            maxWidth: 240,
            width: "max-content",
            zIndex: 1000,
            boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
            pointerEvents: "none",
                        textAlign: "left",
                        lineHeight: 1.3,
                        textTransform: "none",
                        fontWeight: "normal",
                        letterSpacing: "normal",
                      }}
        >
          {text}
        </span>
      )}
    </span>
  );
}
