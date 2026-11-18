package com.fahadali.oneos.core.model

/**
 * A small JSON writer for report.json (no serialization library in
 * :core:model). Values: Map<String, *> (key order kept), List<*>, String,
 * Number, Boolean, null. Output is pretty-printed with two-space indents and
 * ends with a newline.
 */
object Json {
    fun write(value: Any?): String = StringBuilder().also { write(it, value, 0) }.append('\n').toString()

    private fun write(out: StringBuilder, value: Any?, depth: Int) {
        when (value) {
            null -> out.append("null")
            is String -> string(out, value)
            is Boolean -> out.append(value)
            is Int, is Long, is Short, is Byte -> out.append(value.toString())
            is Double -> out.append(if (value.isFinite()) value.toString() else "null")
            is Float -> out.append(if (value.isFinite()) value.toString() else "null")
            is Number -> out.append(value.toString())
            is Map<*, *> -> {
                if (value.isEmpty()) {
                    out.append("{}")
                } else {
                    out.append("{\n")
                    val last = value.size - 1
                    var i = 0
                    for (e in value.entries) {
                        indent(out, depth + 1)
                        string(out, e.key.toString())
                        out.append(": ")
                        write(out, e.value, depth + 1)
                        if (i < last) out.append(',')
                        out.append('\n')
                        i++
                    }
                    indent(out, depth)
                    out.append('}')
                }
            }
            is List<*> -> {
                if (value.isEmpty()) {
                    out.append("[]")
                } else {
                    out.append("[\n")
                    val last = value.size - 1
                    for (i in 0..last) {
                        indent(out, depth + 1)
                        write(out, value[i], depth + 1)
                        if (i < last) out.append(',')
                        out.append('\n')
                    }
                    indent(out, depth)
                    out.append(']')
                }
            }
            is Iterable<*> -> {
                val it = value.iterator()
                if (!it.hasNext()) {
                    out.append("[]")
                } else {
                    out.append("[\n")
                    while (true) {
                        indent(out, depth + 1)
                        write(out, it.next(), depth + 1)
                        val more = it.hasNext()
                        if (more) out.append(',')
                        out.append('\n')
                        if (!more) break
                    }
                    indent(out, depth)
                    out.append(']')
                }
            }
            is Array<*> -> write(out, value.asList(), depth)
            else -> string(out, value.toString())
        }
    }

    private fun indent(out: StringBuilder, depth: Int) {
        if (depth <= INDENTS.lastIndex) out.append(INDENTS[depth]) else repeat(depth) { out.append("  ") }
    }

    fun string(out: StringBuilder, s: String) {
        out.append('"')
        var start = 0
        for (i in s.indices) {
            val c = s[i]
            val esc = when (c) {
                '"' -> "\\\""
                '\\' -> "\\\\"
                '\n' -> "\\n"
                '\r' -> "\\r"
                '\t' -> "\\t"
                '\b' -> "\\b"
                '\u000c' -> "\\f"
                else -> if (c < ' ' || c == '\u2028' || c == '\u2029') "" else null
            }
            if (esc != null) {
                if (i > start) out.append(s, start, i)
                if (esc.isNotEmpty()) {
                    out.append(esc)
                } else {
                    val code = c.code
                    out.append("\\u")
                        .append(HEX[(code ushr 12) and 0xf])
                        .append(HEX[(code ushr 8) and 0xf])
                        .append(HEX[(code ushr 4) and 0xf])
                        .append(HEX[code and 0xf])
                }
                start = i + 1
            }
        }
        if (start < s.length) out.append(s, start, s.length)
        out.append('"')
    }

    private const val HEX = "0123456789abcdef"
    private val INDENTS = Array(16) { "  ".repeat(it) }
}
