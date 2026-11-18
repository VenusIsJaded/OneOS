package com.fahadali.oneos.guest

import android.graphics.drawable.Drawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.NestedScrollView
import com.fahadali.oneos.guest.CompileState.Phase
import com.fahadali.oneos.shared.NativeCards
import dev.oneuiproject.oneui.layout.ToolbarLayout
import dev.oneuiproject.oneui.utils.applyEdgeToEdge

/**
 * Optimize apps: compiles every app in this system ahead of time (`cmd package compile -m speed`, what
 * Samsung's Galaxy App Booster does) so they start faster. The work is done by oneos_compile.sh as root;
 * this screen only asks for it and shows [CompileState]: a progress bar, the app being compiled with its icon,
 * and the last ones done. The system also does it by itself after boot (paced, and while the phone is idle);
 * the button takes over and does it now, without the pacing.
 */
class OptimizeAppsActivity : AppCompatActivity() {
    private val handler = Handler(Looper.getMainLooper())

    /** Epoch seconds of the last request sent from this screen: it shows "Starting…" until the status line is newer. */
    private var askedAt = 0L
    private var askedWhat = ""
    private var shown = ""
    private val labelCache = HashMap<String, String>()
    private val iconCache = HashMap<String, Drawable?>()
    private val poll = object : Runnable {
        override fun run() {
            render(false)
            handler.postDelayed(this, 2000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyEdgeToEdge()
        setContentView(R.layout.activity_guest_optimize)
        val toolbar = findViewById<ToolbarLayout>(R.id.guest_optimize_toolbar)
        toolbar.setTitle(getString(R.string.optimize_title))
        toolbar.showNavigationButtonAsBack = true
        toolbar.setNavigationButtonOnClickListener { onBackPressedDispatcher.onBackPressed() }
        toolbar.setAdaptiveMarginProvider(dev.oneuiproject.oneui.widget.AdaptiveCoordinatorLayout.MARGIN_PROVIDER_ZERO)
        toolbar.setEdgeInsetHorizontal(0f)
        val scroll = findViewById<NestedScrollView>(R.id.guest_optimize_scroll)
        toolbar.setOnApplyWindowInsetsListener { v, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            v.setPadding(bars.left, bars.top, bars.right, 0)
            scroll.setPadding(0, 0, 0, bars.bottom)
            insets
        }
        toolbar.findViewById<dev.oneuiproject.oneui.widget.RoundedFrameLayout>(dev.oneuiproject.oneui.design.R.id.tbl_main_content).apply { roundedCorners = 0; fillHorizontalPadding = false }
        toolbar.findViewById<View>(dev.oneuiproject.oneui.design.R.id.tbl_bottom_corners).visibility = View.GONE
        render(true)
    }

    override fun onResume() {
        super.onResume()
        handler.post(poll)
    }

    override fun onPause() {
        handler.removeCallbacks(poll)
        super.onPause()
    }

    private fun now() = System.currentTimeMillis() / 1000

    private fun label(pkg: String): String =
        labelCache.getOrPut(pkg) {
            runCatching { packageManager.getApplicationInfo(pkg, 0).loadLabel(packageManager).toString() }.getOrDefault(pkg)
        }

    private fun icon(pkg: String): Drawable? =
        if (pkg in iconCache) iconCache[pkg] else runCatching { packageManager.getApplicationIcon(pkg) }.getOrNull().also { iconCache[pkg] = it }

    /** The screen is drawn again only when what it shows changed (a wait or a cool-down rewrites the status every 10-30 s). */
    private fun render(force: Boolean) {
        val snap = CompileState.read()
        val s = snap.status
        val key = listOf(snap.dirExists, s?.phase, s?.total, s?.index, s?.pkg, s?.manual, snap.results.size, askedAt, askedWhat, if (snap.active(now())) 1 else 0).joinToString("|")
        if (!force && key == shown) return
        shown = key
        val list = findViewById<LinearLayout>(R.id.guest_optimize_list)
        list.removeAllViews()
        val color = getColor(R.color.preboot_card)
        val active = snap.active(now())
        val starting = askedAt > 0 && (s == null || s.epoch < askedAt) && now() - askedAt in 0 until 60
        val total = s?.total ?: 0
        val done = snap.results.size.coerceAtMost(total.takeIf { it > 0 } ?: Int.MAX_VALUE)

        val head = NativeCards.section(list, getString(R.string.optimize_intro), color)
        val title: String
        val summary: String?
        when {
            !snap.dirExists && !starting -> { title = getString(R.string.optimize_not_ready); summary = getString(R.string.optimize_not_ready_why) }
            starting -> { title = if (askedWhat == "stop") getString(R.string.optimize_stopping) else getString(R.string.optimize_starting); summary = null }
            s == null -> { title = getString(R.string.optimize_idle); summary = getString(R.string.optimize_idle_why) }
            active && s.phase == Phase.WAITING -> { title = getString(R.string.optimize_waiting, done, total); summary = getString(R.string.optimize_waiting_why) }
            active && s.phase == Phase.COOLING -> { title = getString(R.string.optimize_cooling, done, total); summary = getString(R.string.optimize_cooling_why) }
            active -> { title = getString(R.string.optimize_running, done, total); summary = if (s.manual) null else getString(R.string.optimize_auto_why) }
            s.phase == Phase.DONE -> { title = getString(R.string.optimize_done, total); summary = getString(R.string.optimize_done_why, snap.results.count { !it.ok }) }
            else -> { title = getString(R.string.optimize_stopped, done, total); summary = getString(R.string.optimize_stopped_why) }
        }
        NativeCards.row(head, title, summary)
        if (active && total > 0) {
            val bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
                max = total; progress = done; isIndeterminate = false
                progressTintList = android.content.res.ColorStateList.valueOf(getColor(R.color.preboot_primary))
            }
            val pad = (16 * resources.displayMetrics.density + .5f).toInt()
            head.addView(bar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { setMargins(pad, 0, pad, pad / 2) })
        }
        val manualRun = active && s?.manual == true
        val canAsk = snap.dirExists && !starting
        if (canAsk) {
            val button = LayoutInflater.from(this).inflate(R.layout.item_bring_open, head, false) as Button
            button.text = getString(if (manualRun) R.string.optimize_stop else if (s?.phase == Phase.DONE) R.string.optimize_again else R.string.optimize_start)
            button.setOnClickListener { ask(if (manualRun) "stop" else "now") }
            head.addView(button, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = android.view.Gravity.END })
        }

        // The app being compiled (with its icon), then the last ones finished: not the whole list, it is 200+ apps.
        val cur = s?.pkg
        if (active && cur != null) {
            val card = NativeCards.section(list, getString(R.string.optimize_now), color)
            appRow(card, cur, getString(R.string.optimize_compiling))
        }
        val recent = snap.results.asReversed().take(RECENT)
        if (recent.isNotEmpty()) {
            val card = NativeCards.section(list, getString(R.string.optimize_recent), color)
            for (r in recent) appRow(card, r.pkg, if (r.ok) getString(R.string.optimize_ok, r.seconds) else getString(R.string.optimize_failed))
            val more = snap.results.size - recent.size
            if (more > 0) NativeCards.row(card, getString(R.string.optimize_more, more))
        }
    }

    private fun appRow(card: LinearLayout, pkg: String, summary: String) {
        val row = NativeCards.row(card, label(pkg), summary)
        icon(pkg)?.let { d ->
            row.getIconImageView()?.apply {
                setImageDrawable(d)
                imageTintList = null
                visibility = View.VISIBLE
            }
        }
    }

    private fun ask(what: String) {
        val ok = runCatching { CompileState.send(this, what) }.isSuccess
        if (ok) {
            askedAt = now()
            askedWhat = what
        } else {
            Toast.makeText(this, R.string.optimize_failed_request, Toast.LENGTH_LONG).show()
        }
        render(true)
    }

    private companion object {
        const val RECENT = 12
    }
}
