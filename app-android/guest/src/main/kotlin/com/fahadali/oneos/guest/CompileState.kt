package com.fahadali.oneos.guest

import android.content.Context
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

/**
 * The guest's app compiling (oneos_compile.sh, started by the guest's root loop; 0.5.20), as this app sees it.
 *
 * Reads `/data/oneos_compile/status` ("<state> <total> <index> <package> <mode> <epoch>", state running,
 * cooling, waiting, done or stopped) and `/data/oneos_compile/results` ("<package> ok|fail <seconds>" per
 * finished app), both world readable. The button leaves `files/oneos_compile` ("now" or "stop") in this app's
 * own folder, the way [ReturnRequest] does; the guest's root loop reads it within ten seconds. No root here.
 */
object CompileState {
    const val DIR = "/data/oneos_compile"
    const val REQUEST = "oneos_compile"

    enum class Phase { RUNNING, COOLING, WAITING, DONE, STOPPED }

    class Status(val phase: Phase, val total: Int, val index: Int, val pkg: String?, val manual: Boolean, val epoch: Long)

    class Result(val pkg: String, val ok: Boolean, val seconds: Int)

    class Snapshot(val dirExists: Boolean, val status: Status?, val results: List<Result>) {
        /** Working right now (not stopped, not done, and the status line is not stale: one app can take minutes, a wait or a cool-down rewrites it every 10-30 s). */
        fun active(nowSec: Long): Boolean {
            val s = status ?: return false
            if (s.phase == Phase.DONE || s.phase == Phase.STOPPED) return false
            return nowSec - s.epoch < STALE_SEC
        }
    }

    private const val STALE_SEC = 15 * 60L

    fun parseStatus(line: String?): Status? {
        val f = line?.trim()?.split(' ')?.filter { it.isNotEmpty() } ?: return null
        if (f.size < 6) return null
        val phase = when (f[0]) {
            "running" -> Phase.RUNNING
            "cooling" -> Phase.COOLING
            "waiting" -> Phase.WAITING
            "done" -> Phase.DONE
            "stopped" -> Phase.STOPPED
            else -> return null
        }
        return Status(phase, f[1].toIntOrNull() ?: return null, f[2].toIntOrNull() ?: return null, f[3].takeIf { it != "-" }, f[4] == "now", f[5].toLongOrNull() ?: return null)
    }

    fun parseResults(text: String?): List<Result> {
        if (text.isNullOrEmpty()) return emptyList()
        val out = ArrayList<Result>()
        var start = 0
        val len = text.length
        while (start < len) {
            var end = text.indexOf('\n', start)
            if (end < 0) end = len
            var p = start
            while (p < end && text[p].isWhitespace()) p++
            var e = end
            while (e > p && text[e - 1].isWhitespace()) e--
            if (p < e) {
                val s1 = text.indexOf(' ', p)
                if (s1 in (p + 1) until e) {
                    var v0 = s1 + 1
                    while (v0 < e && text[v0] == ' ') v0++
                    var s2 = text.indexOf(' ', v0)
                    if (s2 < 0 || s2 > e) s2 = e
                    val verdictLen = s2 - v0
                    val ok = verdictLen == 2 && text[v0] == 'o' && text[v0 + 1] == 'k'
                    val fail = !ok && verdictLen == 4 && text.regionMatches(v0, "fail", 0, 4)
                    if (ok || fail) {
                        var sec = 0
                        var t0 = s2 + 1
                        while (t0 < e && text[t0] == ' ') t0++
                        if (t0 < e) {
                            var t1 = text.indexOf(' ', t0)
                            if (t1 < 0 || t1 > e) t1 = e
                            sec = text.substring(t0, t1).toIntOrNull() ?: 0
                        }
                        out.add(Result(text.substring(p, s1), ok, sec))
                    }
                }
            }
            start = end + 1
        }
        return out
    }

    fun read(): Snapshot {
        val dir = File(DIR)
        if (!dir.isDirectory) return Snapshot(false, null, emptyList())
        val statusLine = runCatching { File(dir, "status").takeIf { it.isFile }?.useLines { it.firstOrNull() } }.getOrNull()
        val resultsText = runCatching { File(dir, "results").takeIf { it.isFile }?.readText() }.getOrNull()
        return Snapshot(true, parseStatus(statusLine), parseResults(resultsText))
    }

    fun pending(context: Context): Boolean = File(context.filesDir, REQUEST).isFile

    /** `now` starts (or takes over from the automatic run), `stop` ends it after the app being compiled; written to a temporary name and renamed. */
    @Throws(IOException::class)
    fun send(context: Context, what: String) {
        val dir = context.filesDir
        val tmp = File(dir, "$REQUEST.tmp")
        FileOutputStream(tmp).use { out ->
            out.write("$what ${System.currentTimeMillis() / 1000}\n".toByteArray())
            out.fd.sync()
        }
        if (!tmp.renameTo(File(dir, REQUEST))) {
            tmp.delete()
            throw IOException("could not create ${File(dir, REQUEST)}")
        }
    }
}
