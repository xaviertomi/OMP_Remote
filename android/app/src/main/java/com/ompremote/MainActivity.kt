package com.ompremote

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Looper
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.Locale
import java.util.UUID
import kotlin.math.roundToInt
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private enum class Menu { CONVERSATION, APPLICATION, KILL }
    private data class ConversationChoice(val id: String?, val label: String)

    private lateinit var store: SecureStore
    private lateinit var conversations: ConversationStore
    private val client = ApiClient()
    private val executor = Executors.newCachedThreadPool()

    private lateinit var status: TextView
    private lateinit var conversationView: TextView
    private lateinit var diagnosticsView: TextView
    private lateinit var diagnosticsToggle: Button
    private lateinit var jobsContainer: LinearLayout
    private lateinit var killJobsContainer: LinearLayout
    private lateinit var filesContainer: LinearLayout
    private lateinit var selectedConversationLabel: TextView
    private lateinit var selectedJobLabel: TextView
    private lateinit var conversationSelector: Spinner
    private lateinit var conversationSelectorAdapter: ArrayAdapter<String>
    private lateinit var sendButton: Button
    private lateinit var selectedFileLabel: TextView
    private lateinit var selectedUploadLabel: TextView
    private lateinit var promptField: EditText
    private lateinit var serverField: EditText
    private lateinit var tokenField: EditText
    private lateinit var emergencyField: EditText
    private lateinit var conversationScroll: ScrollView
    private lateinit var applicationScroll: ScrollView
    private lateinit var killScroll: ScrollView
    private lateinit var killSelectedJobLabel: TextView
    private lateinit var cancelSelectedButton: Button
    private lateinit var conversationCancelButton: Button
    private val navigationItems = linkedMapOf<Menu, LinearLayout>()
    private val conversationSelectorIds = mutableListOf<String?>()
    private var suppressConversationSelection = false
    private var allowAutoSelectConversation = true

    private var selectedConversationId: String? = null
    private var selectedJobId: String? = null
    private var selectedFileId: String? = null
    private var selectedUploadUri: Uri? = null
    private var selectedUploadName: String? = null
    private var selectedUploadSize: Long = -1L
    private var selectedFileName: String? = null
    private var selectedFileMime: String = "application/octet-stream"
    private var pendingDownloadBytes: ByteArray? = null
    private var pendingDownloadName: String = "download.bin"
    private var pendingDownloadMime: String = "application/octet-stream"
    private var selectedJobState: String? = null
    @Volatile
    private var submitting = false
    @Volatile
    private var activityActive = false
    @Volatile
    private var conversationEpoch = 0L

    companion object {
        private const val OPEN_DOCUMENT_REQUEST = 4001
        private const val SAVE_DOWNLOAD_REQUEST = 4002
        private const val STATE_SELECTED_CONVERSATION_ID = "selected_conversation_id"
        private const val STATE_SELECTED_JOB_ID = "selected_job_id"
        private const val STATE_PROMPT_DRAFT = "prompt_draft"
        private const val STATE_CONVERSATION_SCROLL_Y = "conversation_scroll_y"
        private const val STATE_ALLOW_AUTO_SELECT_CONVERSATION = "allow_auto_select_conversation"
        private val NAVY = Color.rgb(9, 20, 42)
        private val PANEL = Color.rgb(18, 35, 65)
        private val PANEL_LIGHT = Color.rgb(26, 49, 83)
        private val TEXT = Color.rgb(235, 244, 255)
        private val MUTED = Color.rgb(163, 185, 215)
        private val CYAN = Color.rgb(47, 211, 224)
        private val RED = Color.rgb(255, 111, 118)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        store = SecureStore(this)
        conversations = ConversationStore(this, conversationCacheNamespace())
        val ui = buildUi()
        setContentView(ui)
        selectedConversationId = savedInstanceState?.getString(STATE_SELECTED_CONVERSATION_ID)
        selectedJobId = savedInstanceState?.getString(STATE_SELECTED_JOB_ID)
        allowAutoSelectConversation = savedInstanceState?.getBoolean(
            STATE_ALLOW_AUTO_SELECT_CONVERSATION,
            true,
        ) ?: true
        savedInstanceState?.getString(STATE_PROMPT_DRAFT)?.let { draft ->
            promptField.setText(draft)
            promptField.setSelection(draft.length)
        }
        configureSystemBars(ui)
        activityActive = true
        renderHistory()
        savedInstanceState?.getInt(STATE_CONVERSATION_SCROLL_Y, -1)?.takeIf { it >= 0 }?.let { scrollY ->
            conversationScroll.post { conversationScroll.scrollTo(0, scrollY) }
        }
        if (server().isNotBlank() && normalToken().isNotBlank()) {
            refreshConversations(resumeSelected = true)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString(STATE_SELECTED_CONVERSATION_ID, selectedConversationId)
        outState.putString(STATE_SELECTED_JOB_ID, selectedJobId)
        outState.putBoolean(STATE_ALLOW_AUTO_SELECT_CONVERSATION, allowAutoSelectConversation)
        if (::promptField.isInitialized) outState.putString(STATE_PROMPT_DRAFT, promptField.text.toString())
        if (::conversationScroll.isInitialized) outState.putInt(STATE_CONVERSATION_SCROLL_Y, conversationScroll.scrollY)
        super.onSaveInstanceState(outState)
    }

    @Suppress("DEPRECATION")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == SAVE_DOWNLOAD_REQUEST) {
            val uri = data?.data
            val bytes = pendingDownloadBytes
            pendingDownloadBytes = null
            if (resultCode != RESULT_OK || uri == null || bytes == null) {
                show("Enregistrement du fichier annulé")
                return
            }
            val name = pendingDownloadName
            executor.execute {
                try {
                    contentResolver.openOutputStream(uri)?.use { it.write(bytes) }
                        ?: throw IllegalArgumentException("destination inaccessible")
                    show("Fichier enregistré : $name")
                } catch (error: Exception) {
                    show("Enregistrement échoué : ${error.message ?: "destination inaccessible"}")
                }
            }
            return
        }
        if (requestCode != OPEN_DOCUMENT_REQUEST) return
        if (resultCode != RESULT_OK || data?.data == null) {
            show("Sélection du fichier annulée")
            return
        }
        val uri = requireNotNull(data.data)
        val takeFlags = data.flags and (Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        if (takeFlags != 0) {
            try {
                contentResolver.takePersistableUriPermission(uri, takeFlags)
            } catch (_: Exception) {
                // Some document providers grant a one-shot read permission only.
            }
        }
        describeSelectedFile(uri)
    }

    override fun onDestroy() {
        activityActive = false
        conversationEpoch++
        executor.shutdownNow()
        super.onDestroy()
    }

    private fun configureSystemBars(root: View) {
        window.statusBarColor = NAVY
        window.navigationBarColor = PANEL
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            window.isNavigationBarContrastEnforced = false
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false)
            window.insetsController?.show(WindowInsets.Type.systemBars())
            root.setOnApplyWindowInsetsListener { view, insets ->
                val bars = insets.getInsets(
                    WindowInsets.Type.systemBars() or
                        WindowInsets.Type.displayCutout() or
                        WindowInsets.Type.ime(),
                )
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
                if (::promptField.isInitialized && promptField.hasFocus()) {
                    scrollConversationToBottom()
                }
                insets
            }
            root.requestApplyInsets()
        } else {
            root.fitsSystemWindows = true
        }
    }

    private fun buildUi(): LinearLayout {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(NAVY)
        }
        root.addView(buildHeader())
        status = TextView(this).apply {
            text = "Ready"
            textSize = 13f
            setTextColor(MUTED)
            setPadding(dp(20), dp(8), dp(20), dp(8))
            background = rounded(PANEL, dp(16))
        }
        root.addView(status, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(dp(16), dp(4), dp(16), dp(8))
        })

        val body = FrameLayout(this).apply { setBackgroundColor(NAVY) }
        conversationScroll = buildConversationScreen()
        applicationScroll = buildApplicationScreen()
        killScroll = buildKillScreen()
        body.addView(conversationScroll, frameParams())
        body.addView(applicationScroll, frameParams())
        body.addView(killScroll, frameParams())
        root.addView(body, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        root.addView(bottomNavigation())
        showMenu(Menu.CONVERSATION)
        return root
    }

    private fun buildHeader(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        setPadding(dp(16), dp(12), dp(12), dp(4))
        setBackgroundColor(NAVY)
        addView(ImageView(this@MainActivity).apply {
            contentDescription = null
            setImageResource(R.drawable.omp_logo)
            adjustViewBounds = true
            scaleType = ImageView.ScaleType.CENTER_INSIDE
        }, LinearLayout.LayoutParams(dp(64), dp(64)))
        addView(LinearLayout(this@MainActivity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), 0, dp(8), 0)
            addView(TextView(this@MainActivity).apply {
                text = "OMP Remote v${BuildConfig.VERSION_NAME}"
                textSize = 20f
                typeface = Typeface.DEFAULT_BOLD
                setTextColor(TEXT)
            })
            addView(TextView(this@MainActivity).apply {
                text = "Profil actif · tomi"
                textSize = 13f
                setTextColor(CYAN)
            })
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        addView(Button(this@MainActivity).apply {
            text = "⚙"
            contentDescription = "Settings and updates"
            textSize = 20f
            isAllCaps = false
            setTextColor(TEXT)
            backgroundTintList = ColorStateList.valueOf(PANEL_LIGHT)
            setOnClickListener { showSettings() }
        }, LinearLayout.LayoutParams(dp(54), dp(54)))
    }

    private fun buildConversationScreen(): ScrollView {
        conversationView = TextView(this).apply {
            textSize = 15f
            setTextColor(TEXT)
            setTextIsSelectable(true)
            setPadding(dp(18), dp(18), dp(18), dp(18))
            background = rounded(PANEL, dp(18))
            contentDescription = "Dialogue de la conversation active"
        }
        diagnosticsView = TextView(this).apply {
            textSize = 13f
            setTextColor(MUTED)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            background = rounded(PANEL, dp(16))
            visibility = View.GONE
        }
        diagnosticsToggle = actionButton("Afficher les diagnostics") {
            diagnosticsView.visibility = if (diagnosticsView.visibility == View.VISIBLE) View.GONE else View.VISIBLE
            diagnosticsToggle.text = if (diagnosticsView.visibility == View.VISIBLE) "Masquer les diagnostics" else "Afficher les diagnostics"
        }
        selectedConversationLabel = infoLabel("Aucune conversation sélectionnée")
        selectedJobLabel = infoLabel("Aucun job associé sélectionné")
        conversationSelectorAdapter = ArrayAdapter<String>(this, android.R.layout.simple_spinner_item).apply {
            setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        }
        conversationSelector = Spinner(this).apply {
            contentDescription = "Sélection de la conversation"
            adapter = conversationSelectorAdapter
            minimumHeight = dp(48)
            setPadding(dp(12), 0, dp(12), 0)
            setBackgroundTintList(ColorStateList.valueOf(CYAN))
            onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(
                    parent: android.widget.AdapterView<*>?,
                    view: View?,
                    position: Int,
                    id: Long,
                ) {
                    if (suppressConversationSelection) return
                    val selected = conversationSelectorIds.getOrNull(position)
                    if (selected == selectedConversationId) return
                    if (selected == null) clearConversationSelection(manual = true) else selectConversation(selected)
                }

                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
            }
        }
        jobsContainer = verticalContainer()
        filesContainer = verticalContainer()
        selectedFileLabel = infoLabel("Aucun fichier sélectionné")
        selectedUploadLabel = infoLabel("Aucun upload sélectionné (maximum ${formatSize(ApiClient.MAX_UPLOAD_BYTES)})")
        promptField = field("Écrire un message pour OMP", false).apply {
            id = View.generateViewId()
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            minLines = 3
            gravity = Gravity.TOP
            setTextColor(TEXT)
            setHintTextColor(MUTED)
            setOnFocusChangeListener { _, hasFocus ->
                if (hasFocus) post { scrollConversationToBottom() }
            }
            setOnClickListener { post { scrollConversationToBottom() } }
        }
        serverField = field("URL serveur HTTPS privé", false).apply {
            id = View.generateViewId()
            setText(store.getServerUrl().orEmpty())
        }
        tokenField = field("Credential normal du device", true).apply {
            id = View.generateViewId()
            setText(store.getNormalToken().orEmpty())
        }
        emergencyField = field("Credential emergency séparé", true).apply {
            id = View.generateViewId()
            setText(store.getEmergencyToken().orEmpty())
        }
        sendButton = actionButton("Envoyer le message") {
            val prompt = promptField.text.toString()
            if (prompt.isBlank()) show("Le message est obligatoire") else submitConversation(prompt)
        }

        val content = verticalContent()
        content.addView(sectionLabel("Connexion HTTPS"))
        content.addView(fieldLabel("Adresse du serveur", serverField))
        content.addView(serverField)
        content.addView(fieldLabel("Credential normal", tokenField))
        content.addView(tokenField)
        content.addView(fieldLabel("Credential emergency séparé", emergencyField))
        content.addView(emergencyField)
        content.addView(actionButton("Enregistrer les identifiants") { saveProfile() })
        content.addView(actionButton("Vérifier la connexion HTTPS") { runApi { client.health(server()) } })
        content.addView(sectionLabel("Conversations"))
        content.addView(conversationSelector)
        content.addView(selectedConversationLabel)
        content.addView(actionButton("Actualiser les conversations") { refreshConversations(resumeSelected = true) })
        content.addView(sectionLabel("Dialogue actif"))
        content.addView(conversationView)
        content.addView(sectionLabel("Nouveau message"))
        content.addView(fieldLabel("Message à envoyer", promptField))
        content.addView(promptField)
        content.addView(sendButton)
        content.addView(diagnosticsToggle)
        content.addView(diagnosticsView)
        content.addView(sectionLabel("Job associé sélectionné"))
        content.addView(selectedJobLabel)
        conversationCancelButton = actionButton("Annuler le traitement en cours", danger = true) {
            confirmCancelSelected()
        }.apply {
            isEnabled = false
        }
        content.addView(conversationCancelButton)
        content.addView(actionButton("Actualiser le job associé") { refreshSelected() })
        content.addView(sectionLabel("Jobs serveur"))
        content.addView(actionButton("Actualiser les jobs") { refreshJobs() })
        content.addView(TextView(this).apply {
            text = "L'historique serveur est immuable. Effacer la liste masque seulement l'affichage local."
            textSize = 13f
            setTextColor(MUTED)
            setPadding(8, 4, 8, 4)
        })
        content.addView(actionButton("Effacer les jobs (vue locale)") { confirmClearJobs() })
        content.addView(jobsContainer)
        content.addView(sectionLabel("Fichiers du job sélectionné"))
        content.addView(actionButton("Fichiers du job associé") { loadFiles() })
        content.addView(filesContainer)
        content.addView(selectedFileLabel)
        content.addView(actionButton("Télécharger / enregistrer le fichier") { downloadSelected() })
        content.addView(selectedUploadLabel)
        content.addView(actionButton("Choisir un fichier…") { chooseFile() })
        content.addView(actionButton("Envoyer le fichier au job associé") { uploadSelected() })
        content.addView(actionButton("Effacer l'historique local") { confirmClearConversation() })
        return ScrollView(this).apply {
            isFillViewport = true
            descendantFocusability = ViewGroup.FOCUS_AFTER_DESCENDANTS
            setPadding(dp(16), dp(4), dp(16), dp(12))
            addView(content)
        }
    }

    private fun buildApplicationScreen(): ScrollView {
        val content = verticalContent().apply { gravity = Gravity.CENTER }
        content.addView(TextView(this).apply {
            text = "Les fonctionnalités à venir apparaîtront ici."
            textSize = 16f
            setTextColor(TEXT)
            gravity = Gravity.CENTER
            setPadding(dp(24), dp(32), dp(24), dp(32))
            background = rounded(PANEL, dp(18))
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, dp(80), 0, 0)
        })
        return ScrollView(this).apply {
            isFillViewport = true
            setPadding(dp(16), dp(4), dp(16), dp(12))
            addView(content)
        }
    }

    private fun buildKillScreen(): ScrollView {
        killJobsContainer = verticalContainer()
        val content = verticalContent()
        content.addView(sectionLabel("Kill", danger = true))
        content.addView(TextView(this).apply {
            text = "Sélectionnez un job queued ou running pour l'annuler. L'ouverture de cet écran n'annule rien."
            textSize = 14f
            setTextColor(MUTED)
            setPadding(dp(8), dp(4), dp(8), dp(12))
        })
        content.addView(actionButton("Actualiser les jobs annulables") { refreshJobs() })
        content.addView(killJobsContainer)
        killSelectedJobLabel = infoLabel("Aucun job suivi")
        content.addView(killSelectedJobLabel)
        cancelSelectedButton = actionButton("Annuler le job sélectionné", danger = true) { confirmCancelSelected() }.apply {
            isEnabled = false
        }
        content.addView(cancelSelectedButton)
        content.addView(sectionLabel("Arrêt d'urgence", danger = true))
        content.addView(TextView(this).apply {
            text = "L'arrêt d'urgence désactive OMP Remote, arrête ses workers et son API, puis conserve uniquement le statut d'urgence. Il ne réactive rien et ne cible pas les services tiers."
            textSize = 14f
            setTextColor(MUTED)
            setPadding(dp(8), dp(4), dp(8), dp(12))
            background = rounded(PANEL, 16)
        })
        content.addView(actionButton("Statut emergency") { runEmergency { client.emergencyStatus(server(), emergencyToken()) } })
        content.addView(actionButton("KILL OMP REMOTE", danger = true) { confirmEmergencyKill() })
        return ScrollView(this).apply {
            isFillViewport = true
            setPadding(dp(16), dp(4), dp(16), dp(12))
            addView(content)
        }
    }

    private fun bottomNavigation(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        setPadding(dp(8), dp(6), dp(8), dp(8))
        setBackgroundColor(PANEL)
        navigationItems[Menu.CONVERSATION] = navItem("◉", "Conversation", Menu.CONVERSATION)
        navigationItems[Menu.APPLICATION] = navItem("▦", "Application", Menu.APPLICATION)
        navigationItems[Menu.KILL] = navItem("!", "Kill", Menu.KILL)
        addView(navigationItems.getValue(Menu.CONVERSATION), navParams())
        addView(navigationItems.getValue(Menu.APPLICATION), navParams())
        addView(navigationItems.getValue(Menu.KILL), navParams())
    }

    private fun navItem(icon: String, label: String, menu: Menu): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        contentDescription = label
        tag = label
        isFocusable = true
        setPadding(dp(4), dp(4), dp(4), dp(4))
        addView(TextView(this@MainActivity).apply {
            text = icon
            textSize = 20f
            gravity = Gravity.CENTER
            setTextColor(TEXT)
        })
        addView(TextView(this@MainActivity).apply {
            text = label
            textSize = 12f
            gravity = Gravity.CENTER
            setTextColor(TEXT)
        })
        setOnClickListener { showMenu(menu) }
    }

    private fun showMenu(menu: Menu) {
        conversationScroll.visibility = if (menu == Menu.CONVERSATION) View.VISIBLE else View.GONE
        applicationScroll.visibility = if (menu == Menu.APPLICATION) View.VISIBLE else View.GONE
        killScroll.visibility = if (menu == Menu.KILL) View.VISIBLE else View.GONE
        navigationItems.forEach { (itemMenu, item) ->
            item.contentDescription = item.tag?.toString().orEmpty()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                item.stateDescription = if (itemMenu == menu) "sélectionné" else "non sélectionné"
            }
            item.background = rounded(if (itemMenu == menu) Color.rgb(28, 104, 124) else PANEL, dp(14))
        }
    }

    private fun verticalContent(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(0, dp(8), 0, dp(8))
    }

    private fun verticalContainer(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(0, dp(2), 0, dp(2))
    }

    private fun frameParams() = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)

    private fun navParams() = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)

    private fun sectionLabel(label: String, danger: Boolean = false): TextView = TextView(this).apply {
        text = label
        textSize = 18f
        typeface = Typeface.DEFAULT_BOLD
        setTextColor(if (danger) RED else TEXT)
        setPadding(dp(4), dp(18), dp(4), dp(8))
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) isAccessibilityHeading = true
    }

    private fun infoLabel(textValue: String): TextView = TextView(this).apply {
        text = textValue
        textSize = 14f
        setTextColor(CYAN)
        setPadding(dp(16), dp(12), dp(16), dp(12))
        background = rounded(PANEL_LIGHT, dp(16))
    }


    private fun fieldLabel(label: String, target: View? = null): TextView = TextView(this).apply {
        text = label
        textSize = 12f
        setTextColor(MUTED)
        target?.let { labelFor = it.id }
        setPadding(dp(4), dp(8), dp(4), dp(2))
    }
    private fun rounded(color: Int, radius: Int): GradientDrawable = GradientDrawable().apply {
        setColor(color)
        cornerRadius = radius.toFloat()
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).roundToInt()

    private fun field(label: String, secret: Boolean): EditText = EditText(this).apply {
        hint = label
        inputType = if (secret) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        setPadding(dp(16), dp(12), dp(16), dp(12))
        setTextColor(TEXT)
        setHintTextColor(MUTED)
        background = rounded(PANEL_LIGHT, dp(14))
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, dp(5), 0, dp(5))
        }
    }

    private fun actionButton(label: String, danger: Boolean = false, action: () -> Unit): Button = Button(this).apply {
        text = label
        isAllCaps = false
        minHeight = dp(48)
        val primary = label.contains("Envoyer") || label.contains("Enregistrer") ||
            label.contains("Vérifier") || label.contains("Télécharger")
        setTextColor(if (danger || primary) NAVY else CYAN)
        backgroundTintList = ColorStateList.valueOf(if (danger) RED else if (primary) CYAN else PANEL_LIGHT)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            setMargins(0, dp(6), 0, dp(6))
        }
        setOnClickListener { action() }
    }

    private fun server(): String = if (::serverField.isInitialized) serverField.text.toString().trim() else store.getServerUrl().orEmpty()
    private fun normalToken(): String = if (::tokenField.isInitialized) tokenField.text.toString() else store.getNormalToken().orEmpty()
    private fun emergencyToken(): String = if (::emergencyField.isInitialized) emergencyField.text.toString() else store.getEmergencyToken().orEmpty()

    private fun conversationCacheNamespace(): String {
        val identity = "${store.getServerUrl().orEmpty()}\u0000${store.getNormalToken().orEmpty()}"
        return MessageDigest.getInstance("SHA-256")
            .digest(identity.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(Locale.US, it.toInt() and 0xff) }
    }

    private fun show(message: String) {
        if (!activityActive) return
        runOnUiThread {
            if (activityActive && ::status.isInitialized) status.text = message
        }
    }
    private fun scrollConversationToBottom() {
        if (!::conversationScroll.isInitialized || !::sendButton.isInitialized) return
        conversationScroll.postDelayed({
            if (!activityActive) return@postDelayed
            val composerBottom = sendButton.bottom + dp(16)
            val targetY = (composerBottom - conversationScroll.height).coerceAtLeast(0)
            conversationScroll.smoothScrollTo(0, targetY)
        }, 80L)
    }

    private fun runApi(call: () -> ApiClient.Result) {
        show("Working…")
        executor.execute {
            try {
                val result = call()
                show("HTTP ${result.code}: ${result.body.take(500)}")
            } catch (error: Exception) {
                show("Request failed: ${error.message ?: "network error"}")
            }
        }
    }

    private fun runEmergency(call: () -> ApiClient.Result) = runApi(call)

    private fun saveProfile() {
        store.putServerUrl(server())
        store.putNormalToken(normalToken())
        store.putEmergencyToken(emergencyToken())
        conversationEpoch++
        conversations = ConversationStore(this, conversationCacheNamespace())
        selectedConversationId = null
        allowAutoSelectConversation = false
        clearJobSelection(manual = true)
        renderHistory()
        show("Identifiants enregistrés")
        if (server().isNotBlank() && normalToken().isNotBlank()) {
            refreshConversations(resumeSelected = false)
        }

    }
    private fun showSettings() {
        val panel = verticalContent().apply {
            setPadding(24, 4, 24, 4)
            addView(TextView(this@MainActivity).apply {
                text = "Les champs de connexion HTTPS restent visibles en haut de Conversation."
                textSize = 14f
                setTextColor(MUTED)
                setPadding(4, 8, 4, 8)
            })
            addView(actionButton("Enregistrer les identifiants") { saveProfile() })
            addView(actionButton("Vérifier HTTPS") { runApi { client.health(server()) } })
            addView(actionButton("Rechercher une mise à jour") { checkForUpdate() })
            addView(TextView(this@MainActivity).apply {
                text = "Version installée : ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})"
                textSize = 13f
                setTextColor(MUTED)
                setPadding(4, 12, 4, 4)
            })
        }
        AlertDialog.Builder(this)
            .setTitle("Réglages")
            .setView(panel)
            .setPositiveButton("Fermer", null)
            .show()
    }

    private fun submitConversation(prompt: String) {
        synchronized(this) {
            if (submitting) {
                show("Un envoi est déjà en cours")
                return
            }
            submitting = true
        }
        if (::sendButton.isInitialized) sendButton.isEnabled = false
        val targetConversationId = selectedConversationId
        val epoch = conversationEpoch
        val cache = conversations
        show(if (targetConversationId == null) "Création de la conversation…" else "Envoi dans la conversation…")
        executor.execute {
            try {
                val result = if (targetConversationId == null) {
                    client.createConversation(server(), normalToken(), prompt)
                } else {
                    client.addConversationMessage(server(), normalToken(), targetConversationId, prompt)
                }
                if (result.code !in 200..299) {
                    show("Conversation HTTP ${result.code}: ${result.body.take(500)}")
                    return@execute
                }
                val responseConversationId = extractConversationId(result.body)
                    ?: targetConversationId
                    ?: throw IllegalArgumentException("réponse serveur sans identifiant de conversation")
                if (targetConversationId != null && responseConversationId != targetConversationId) {
                    throw IllegalStateException("le serveur a renvoyé une autre conversation")
                }
                if (epoch == conversationEpoch) {
                    parseConversationResponse(result.body, responseConversationId)?.let { detail ->
                        synchronized(cache) { cache.upsert(detail) }
                    }
                }
                if (epoch == conversationEpoch) {
                    runOnUiThread {
                        if (!activityActive || epoch != conversationEpoch) return@runOnUiThread
                        if (selectedConversationId == targetConversationId) {
                            selectedConversationId = responseConversationId
                            allowAutoSelectConversation = false
                            promptField.setText("")
                            renderHistory()
                        }
                    }
                }
                refreshConversationDetail(responseConversationId, epoch, startPolling = true)
            } catch (error: Exception) {
                show("Envoi de conversation échoué : ${error.message ?: "erreur réseau"}")
            } finally {
                runOnUiThread {
                    submitting = false
                    if (activityActive && ::sendButton.isInitialized) sendButton.isEnabled = true
                }
            }
        }
    }

    private fun refreshConversations(resumeSelected: Boolean) {
        if (server().isBlank()) {
            show("Renseignez d'abord l'URL HTTPS du serveur")
            return
        }
        if (normalToken().isBlank()) {
            show("Renseignez d'abord le credential normal")
            return
        }
        val epoch = conversationEpoch
        val cache = conversations
        show("Chargement des conversations…")
        executor.execute {
            try {
                val result = client.listConversations(server(), normalToken())
                if (result.code !in 200..299) {
                    show("Conversations HTTP ${result.code}: ${result.body.take(500)}")
                    return@execute
                }
                val array = JSONObject(result.body).optJSONArray("conversations")
                    ?: throw IllegalArgumentException("réponse sans liste conversations")
                if (epoch != conversationEpoch || cache !== conversations) return@execute
                for (index in array.length() - 1 downTo 0) {
                    val item = array.optJSONObject(index) ?: continue
                    val id = item.optString("id").trim()
                    if (id.isBlank()) continue
                    cache.upsertSummary(
                        id = id,
                        title = item.optString("title"),
                        updatedAt = item.optString("updated_at"),
                    )
                }
                runOnUiThread {
                    if (!activityActive || epoch != conversationEpoch) return@runOnUiThread
                    renderHistory()
                }
                if (resumeSelected) {
                    val id = selectedConversationId ?: conversations.all().firstOrNull()?.id
                    if (id != null) refreshConversationDetail(id, epoch, startPolling = true)
                }
                show("Conversations actualisées")
            } catch (error: Exception) {
                show("Chargement des conversations échoué : ${error.message ?: "erreur réseau"}")
            }
        }
    }

    private fun refreshConversationDetail(conversationId: String, epoch: Long, startPolling: Boolean) {
        executor.execute {
            try {
                val result = client.getConversation(server(), normalToken(), conversationId)
                if (result.code !in 200..299) {
                    show("Conversation HTTP ${result.code}: ${result.body.take(500)}")
                    return@execute
                }
                val detail = parseConversationResponse(result.body, conversationId)
                    ?: throw IllegalArgumentException("réponse détail sans messages")
                if (!acceptConversationDetail(detail, epoch)) return@execute
                showConversationStatus(conversationId, detail.messages.lastOrNull()?.state ?: "vide")
                if (startPolling && detail.messages.any { isPending(it.state) }) {
                    startConversationPolling(conversationId, epoch)
                }
            } catch (error: Exception) {
                show("Actualisation de la conversation échouée : ${error.message ?: "erreur réseau"}")
            }
        }
    }

    private val pollingConversations = mutableMapOf<String, Long>()

    private fun startConversationPolling(conversationId: String, epoch: Long) {
        synchronized(pollingConversations) {
            if (pollingConversations[conversationId] == epoch) return
            pollingConversations[conversationId] = epoch
        }
        executor.execute {
            try {
                var attempts = 0
                while (attempts++ < 1800 && epoch == conversationEpoch) {
                    val result = client.getConversation(server(), normalToken(), conversationId)
                    if (result.code !in 200..299) {
                        show("Conversation HTTP ${result.code}: ${result.body.take(500)}")
                        return@execute
                    }
                    val detail = parseConversationResponse(result.body, conversationId)
                        ?: throw IllegalArgumentException("réponse détail sans messages")
                    if (!acceptConversationDetail(detail, epoch)) return@execute
                    val latest = detail.messages.lastOrNull()
                    showConversationStatus(conversationId, latest?.state ?: "vide")
                    if (detail.messages.none { isPending(it.state) }) return@execute
                    Thread.sleep(2_000)
                }
                if (attempts >= 1800) show("Polling de conversation expiré")
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            } catch (error: Exception) {
                show("Polling de conversation échoué : ${error.message ?: "erreur réseau"}")
            } finally {
                synchronized(pollingConversations) {
                    if (pollingConversations[conversationId] == epoch) pollingConversations.remove(conversationId)
                }
            }
        }
    }

    private fun acceptConversationDetail(detail: ConversationStore.Conversation, epoch: Long): Boolean {
        if (epoch != conversationEpoch) return false
        synchronized(conversations) { conversations.upsert(detail) }
        runOnUiThread {
            if (!activityActive || epoch != conversationEpoch) return@runOnUiThread
            renderHistory()
        }
        return true
    }

    private fun parseConversationResponse(body: String, fallbackId: String): ConversationStore.Conversation? {
        val root = JSONObject(body)
        val wrapped = root.optJSONObject("conversation")
        val source = wrapped ?: root
        val id = source.optString("id").trim().ifBlank {
            root.optString("conversation_id").trim().ifBlank { fallbackId }
        }
        if (id.isBlank()) return null
        val messagesArray = source.optJSONArray("messages") ?: root.optJSONArray("messages")
        val messages = if (messagesArray != null) {
            parseMessages(messagesArray)
        } else {
            root.optJSONObject("message")?.let { parseMessage(it) }?.let(::listOf).orEmpty()
        }
        return ConversationStore.Conversation(
            id = id,
            title = source.optString("title"),
            updatedAt = source.optString("updated_at"),
            messages = messages,
        )
    }

    private fun parseMessages(array: JSONArray): List<ConversationStore.Message> = buildList {
        for (index in 0 until array.length()) {
            val item = array.optJSONObject(index) ?: continue
            add(parseMessage(item))
        }
    }

    private fun parseMessage(item: JSONObject): ConversationStore.Message {
        val job = item.optJSONObject("job")
        return ConversationStore.Message(
            prompt = item.optString("prompt").ifBlank { job?.optString("prompt").orEmpty() },
            jobId = item.optString("job_id").takeIf { it.isNotBlank() && it != "null" }
                ?: job?.optString("id")?.takeIf { it.isNotBlank() && it != "null" },
            state = item.optString("state").ifBlank { job?.optString("state") ?: "unknown" },
            stdout = item.optString("stdout"),
            stderr = item.optString("stderr"),
            error = item.optString("error").takeIf { it.isNotBlank() && it != "null" },
        )
    }

    private fun extractConversationId(body: String): String? {
        val root = JSONObject(body)
        val conversation = root.optJSONObject("conversation")
        return conversation?.optString("id")?.takeIf { it.isNotBlank() }
            ?: root.optString("conversation_id").takeIf { it.isNotBlank() }
            ?: root.optJSONObject("message")?.optString("conversation_id")?.takeIf { it.isNotBlank() }
    }

    private fun isPending(state: String): Boolean = state.lowercase(Locale.ROOT) in setOf("queued", "running", "pending")

    private fun showConversationStatus(conversationId: String, state: String) {
        runOnUiThread {
            if (activityActive && selectedConversationId == conversationId) {
                status.text = "Conversation ${conversationId.take(8)}… · $state"
            }
        }
    }

    private fun renderHistory() {
        if (!activityActive) return
        if (Looper.myLooper() != Looper.getMainLooper()) {
            runOnUiThread { renderHistory() }
            return
        }
        if (!::conversationView.isInitialized) return
        val all = conversations.all()
        if (selectedConversationId == null && allowAutoSelectConversation) {
            selectedConversationId = all.firstOrNull()?.id
        }
        val active = selectedConversationId?.let { id -> all.firstOrNull { it.id == id } }
        val selectedJob = selectedJobId
        val latestJob = active?.messages?.asReversed()?.firstOrNull { it.jobId != null }?.jobId
        if (selectedJob == null) selectedJobId = latestJob
        val selectedMessage = active?.messages?.asReversed()?.firstOrNull { it.jobId == selectedJobId }
            ?: active?.messages?.lastOrNull()
        selectedMessage?.let { message ->
            if (message.jobId == selectedJobId) selectedJobState = message.state
        }
        selectedConversationLabel.text = if (active == null) {
            "Aucune conversation sélectionnée · le prochain message en créera une"
        } else {
            "Conversation active : ${active.id}"
        }
        selectedJobLabel.text = if (selectedJobId != null) {
            val state = selectedMessage?.let { if (it.jobId == selectedJobId) it.state else null } ?: selectedJobState ?: "sélectionné"
            "Job associé : $selectedJobId · $state"
        } else {
            "Aucun job associé sélectionné"
        }
        conversationView.text = when {
            active == null && all.isEmpty() -> "Aucune conversation. Envoyez un message pour commencer."
            active == null -> "Sélectionnez une conversation pour afficher le dialogue."
            active.messages.isEmpty() -> "Conversation ${active.id}\n\nAucun message reçu. Actualisation en cours…"
            else -> active.messages.mapIndexed { index, message ->
                val job = message.jobId ?: "aucun job"
                val output = message.stdout.ifBlank { "(aucune sortie OMP)" }
                val error = message.error?.takeIf { it.isNotBlank() }?.let { "\n\nErreur :\n$it" }.orEmpty()
                "Tour ${index + 1}\n\nVous :\n${message.prompt}\n\nJob associé :\n$job\n\nOMP Remote : ${message.state}\n\nOMP :\n$output$error"
            }.joinToString("\n\n────────────\n\n")
        }
        val stderr = selectedMessage?.stderr.orEmpty()
        val error = selectedMessage?.error?.takeIf { it.isNotBlank() }
        diagnosticsView.text = when {
            error != null -> "Erreur :\n$error${if (stderr.isBlank()) "" else "\n\nstderr :\n$stderr"}"
            stderr.isBlank() -> "Aucun diagnostic stderr pour le job associé."
            else -> "stderr :\n$stderr"
        }
        diagnosticsToggle.visibility = if (selectedMessage == null) View.GONE else View.VISIBLE
        diagnosticsView.visibility = if (selectedMessage == null) View.GONE else diagnosticsView.visibility
        updateConversationSelector()
        updateCancelButton()
    }

    private fun updateConversationSelector() {
        if (!::conversationSelector.isInitialized) return
        val choices = mutableListOf(ConversationChoice(null, "Nouvelle conversation"))
        conversations.all().forEach { conversation ->
            val title = conversation.title.trim().ifBlank { "Conversation ${conversation.id.take(8)}…" }
            val count = conversation.messages.size.takeIf { it > 0 }?.let { " · $it tour(s)" }.orEmpty()
            choices += ConversationChoice(conversation.id, "$title$count")
        }
        selectedConversationId?.let { selectedId ->
            if (choices.none { it.id == selectedId }) {
                choices += ConversationChoice(selectedId, "Conversation ${selectedId.take(8)}… · chargement")
            }
        }
        suppressConversationSelection = true
        conversationSelectorIds.clear()
        conversationSelectorIds.addAll(choices.map { it.id })
        conversationSelectorAdapter.clear()
        conversationSelectorAdapter.addAll(choices.map { it.label })
        conversationSelectorAdapter.notifyDataSetChanged()
        val selectedIndex = choices.indexOfFirst { it.id == selectedConversationId }.let { if (it < 0) 0 else it }
        conversationSelector.setSelection(selectedIndex, false)
        suppressConversationSelection = false
        conversationSelector.contentDescription = "Conversation : ${choices[selectedIndex].label}"
    }

    private fun confirmClearConversation() {
        AlertDialog.Builder(this)
            .setTitle("Effacer l'historique local ?")
            .setMessage("Cela supprime les conversations enregistrées sur ce téléphone. Les conversations, jobs et logs serveur restent intacts.")
            .setNegativeButton("Annuler", null)
            .setPositiveButton("Effacer") { _, _ ->
                synchronized(conversations) {
                    conversationEpoch++
                    conversations.clear()
                }
                selectedConversationId = null
                allowAutoSelectConversation = false
                clearJobSelection(manual = true)
                renderHistory()
                show("Historique local effacé")
            }
            .show()
    }

    private fun selectConversation(conversationId: String) {
        allowAutoSelectConversation = false
        selectedConversationId = conversationId
        clearJobSelection(manual = true)
        selectedConversationLabel.text = "Conversation active : $conversationId · chargement…"
        renderHistory()
        val epoch = conversationEpoch
        refreshConversationDetail(conversationId, epoch, startPolling = true)
    }

    private fun clearConversationSelection(manual: Boolean) {
        selectedConversationId = null
        allowAutoSelectConversation = !manual
        clearJobSelection(manual = manual)
        renderHistory()
    }

    private fun confirmClearJobs() {
        AlertDialog.Builder(this)
            .setTitle("Effacer la liste locale ?")
            .setMessage("L'historique serveur ne sera pas supprimé. Cette action masque uniquement les jobs jusqu'au prochain rafraîchissement.")
            .setNegativeButton("Annuler", null)
            .setPositiveButton("Effacer la vue") { _, _ ->
                jobsContainer.removeAllViews()
                killJobsContainer.removeAllViews()
                jobsContainer.addView(TextView(this).apply { text = "Liste masquée localement. Actualisez pour la recharger."; setTextColor(MUTED) })
                killJobsContainer.addView(TextView(this).apply { text = "Liste masquée localement. Actualisez pour la recharger."; setTextColor(MUTED) })
                show("Liste locale effacée")
            }
            .show()
    }

    private fun updateSelectedJobLabel(text: String) {
        if (::selectedJobLabel.isInitialized) selectedJobLabel.text = text
        if (::killSelectedJobLabel.isInitialized) killSelectedJobLabel.text = text
    }

    private fun clearJobSelection(manual: Boolean) {
        selectedJobId = null
        selectedJobState = null
        selectedFileId = null
        selectedFileName = null
        selectedFileMime = "application/octet-stream"
        updateSelectedJobLabel("Aucun job associé sélectionné")
        if (::selectedFileLabel.isInitialized) selectedFileLabel.text = "Aucun fichier sélectionné"
        if (::filesContainer.isInitialized) filesContainer.removeAllViews()
        if (::diagnosticsView.isInitialized) diagnosticsView.visibility = View.GONE
        if (::diagnosticsToggle.isInitialized) diagnosticsToggle.text = "Afficher les diagnostics"
        updateCancelButton()
    }

    private fun chooseFile() {
        try {
            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                type = "*/*"
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
            }, OPEN_DOCUMENT_REQUEST)
        } catch (error: Exception) {
            show("File picker unavailable: ${error.message ?: "no document provider"}")
        }
    }

    private fun describeSelectedFile(uri: Uri) {
        var name = uri.lastPathSegment?.substringAfterLast('/').orEmpty()
        var size = -1L
        try {
            contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                    if (nameIndex >= 0) name = cursor.getString(nameIndex).orEmpty()
                    if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) size = cursor.getLong(sizeIndex)
                }
            }
        } catch (error: Exception) {
            clearUploadSelection()
            show("Could not inspect selected file: ${error.message ?: "read error"}")
            return
        }
        name = name.trim().substringAfterLast('/').substringAfterLast('\\')
        if (name.isBlank()) name = "upload.bin"
        if (size > ApiClient.MAX_UPLOAD_BYTES) {
            clearUploadSelection()
            show("Selected file is ${formatSize(size)}; maximum is ${formatSize(ApiClient.MAX_UPLOAD_BYTES)}")
            return
        }
        selectedUploadUri = uri
        selectedUploadName = name
        selectedUploadSize = size
        selectedUploadLabel.text = if (size >= 0) "Fichier sélectionné : $name (${formatSize(size)})" else "Fichier sélectionné : $name (taille vérifiée avant upload)"
        show("Fichier sélectionné : $name")
    }

    private fun clearUploadSelection() {
        selectedUploadUri = null
        selectedUploadName = null
        selectedUploadSize = -1L
        if (::selectedUploadLabel.isInitialized) selectedUploadLabel.text = "Aucun upload sélectionné (maximum ${formatSize(ApiClient.MAX_UPLOAD_BYTES)})"
    }

    private fun formatSize(bytes: Long): String {
        if (bytes < 0) return "taille inconnue"
        if (bytes < 1024L) return "$bytes B"
        if (bytes < 1024L * 1024L) return String.format(Locale.US, "%.1f KiB", bytes / 1024.0)
        return String.format(Locale.US, "%.1f MiB", bytes / (1024.0 * 1024.0))
    }

    private fun refreshJobs() {
        runApi {
            val result = client.listJobs(server(), normalToken())
            if (result.code in 200..299) {
                val jobs = JSONObject(result.body).optJSONArray("jobs") ?: JSONArray()
                runOnUiThread {
                    if (activityActive) renderJobs(jobs)
                }
            }
            result
        }
    }

    private fun renderJobs(jobs: JSONArray) {
        jobsContainer.removeAllViews()
        killJobsContainer.removeAllViews()
        if (jobs.length() == 0) {
            jobsContainer.addView(TextView(this).apply { text = "Aucun job serveur"; setTextColor(MUTED) })
            killJobsContainer.addView(TextView(this).apply { text = "Aucun job annulable"; setTextColor(MUTED) })
            updateConversationSelector()
            return
        }
        var cancellable = 0
        for (index in 0 until jobs.length()) {
            val job = jobs.getJSONObject(index)
            val id = job.getString("id")
            val state = job.optString("state", "unknown")
            if (id == selectedJobId) {
                selectedJobState = state
                updateCancelButton()
            }
            jobsContainer.addView(actionButton("${state.uppercase()}  ${id.take(8)}…") { selectJob(id) })
            if (state == "queued" || state == "running") {
                cancellable++
                killJobsContainer.addView(actionButton("${state.uppercase()}  $id") { selectJob(id) })
            }
        }
        if (cancellable == 0) killJobsContainer.addView(TextView(this).apply { text = "Aucun job queued ou running"; setTextColor(MUTED) })
        updateConversationSelector()
    }

    private fun selectJob(jobId: String) {
        val conversation = conversations.all().firstOrNull { candidate ->
            candidate.messages.any { it.jobId == jobId }
        }
        if (conversation != null && selectedConversationId != conversation.id) {
            allowAutoSelectConversation = false
            selectedConversationId = conversation.id
        }
        selectedJobId = jobId
        selectedJobState = conversation?.messages?.firstOrNull { it.jobId == jobId }?.state
        selectedFileId = null
        updateSelectedJobLabel("Job associé sélectionné : $jobId")
        selectedFileLabel.text = "Aucun fichier sélectionné"
        filesContainer.removeAllViews()
        diagnosticsView.visibility = View.GONE
        diagnosticsToggle.text = "Afficher les diagnostics"
        updateCancelButton()
        renderHistory()
        refreshSelected()

    }
    private fun updateCancelButton() {
        val cancellable = selectedJobState == "queued" || selectedJobState == "running"
        if (::cancelSelectedButton.isInitialized) cancelSelectedButton.isEnabled = cancellable
        if (::conversationCancelButton.isInitialized) conversationCancelButton.isEnabled = cancellable
    }

    private fun refreshSelected() {
        val jobId = selectedJobId ?: run { show("Sélectionnez d'abord un job associé"); return }
        val targetConversationId = selectedConversationId
        val epoch = conversationEpoch
        val cache = conversations
        executor.execute {
            try {
                val jobResult = client.getJob(server(), normalToken(), jobId)
                val logResult = client.logs(server(), normalToken(), jobId)
                if (jobResult.code !in 200..299) {
                    show("Job HTTP ${jobResult.code}: ${jobResult.body.take(500)}")
                    return@execute
                }
                val job = JSONObject(jobResult.body).getJSONObject("job")
                val logs = if (logResult.code in 200..299) JSONObject(logResult.body) else JSONObject()
                val prompt = job.optString("prompt")
                val state = job.optString("state", "unknown")
                val stdout = logs.optString("stdout")
                val stderr = logs.optString("stderr")
                val error = job.optString("error").takeIf { it.isNotBlank() && it != "null" }
                if (epoch == conversationEpoch && cache === conversations) {
                    targetConversationId?.let { conversationId ->
                        synchronized(cache) {
                            cache.get(conversationId)?.let { conversation ->
                                val messages = conversation.messages.map { message ->
                                    if (message.jobId == jobId) {
                                        message.copy(
                                            prompt = prompt.ifBlank { message.prompt },
                                            state = state,
                                            stdout = stdout,
                                            stderr = stderr,
                                            error = error,
                                        )
                                    } else {
                                        message
                                    }
                                }
                                cache.upsert(conversation.copy(messages = messages))
                            }
                        }
                    }
                }
                runOnUiThread {
                    if (!activityActive || epoch != conversationEpoch || selectedJobId != jobId) return@runOnUiThread
                    selectedJobState = state
                    updateCancelButton()
                    updateSelectedJobLabel("Job associé sélectionné : $jobId · $state")
                    renderHistory()
                }
                show(
                    if (logResult.code in 200..299) "Job $state · logs actualisés"
                    else "Job $state · logs HTTP ${logResult.code}",
                )
            } catch (error: Exception) {
                show("Actualisation du job échouée : ${error.message ?: "erreur réseau"}")
            }
        }
    }


    private fun confirmCancelSelected() {
        val jobId = selectedJobId ?: run { show("Sélectionnez d'abord un job"); return }
        AlertDialog.Builder(this)
            .setTitle("Annuler ce job ?")
            .setMessage("Cette action cible uniquement le job $jobId. Elle ne déclenche pas l'arrêt d'urgence.")
            .setNegativeButton("Non", null)
            .setPositiveButton("Annuler le job") { _, _ -> cancelSelected(jobId) }
            .show()
    }

    private fun cancelSelected(jobId: String) {
        show("Vérification de l'état du job…")
        executor.execute {
            try {
                val current = client.getJob(server(), normalToken(), jobId)
                if (current.code !in 200..299) {
                    show("Job HTTP ${current.code}")
                    return@execute
                }
                val state = JSONObject(current.body).getJSONObject("job").optString("state")
                if (state != "queued" && state != "running") {
                    show("Annulation disponible uniquement pour un job queued ou running (état : $state)")
                    runOnUiThread {
                        if (activityActive) {
                            selectedJobState = state
                            updateCancelButton()
                        }
                    }
                    return@execute
                }
                val result = client.cancelJob(server(), normalToken(), jobId)
                show("HTTP ${result.code}: ${result.body.take(500)}")
                if (result.code in 200..299) {
                    runOnUiThread {
                        if (activityActive) refreshSelected()
                    }
                }
            } catch (error: Exception) {
                show("Cancel failed: ${error.message ?: "network error"}")
            }
        }
    }

    private fun loadFiles() {
        val jobId = selectedJobId ?: run { show("Sélectionnez d'abord un job"); return }
        show("Chargement des fichiers…")
        executor.execute {
            try {
                val result = client.files(server(), normalToken(), jobId)
                if (result.code !in 200..299) {
                    show("Fichiers HTTP ${result.code}: ${result.body.take(500)}")
                    return@execute
                }
                val files = JSONObject(result.body).optJSONArray("files") ?: JSONArray()
                runOnUiThread {
                    if (!activityActive) return@runOnUiThread
                    filesContainer.removeAllViews()
                    if (files.length() == 0) {
                        filesContainer.addView(TextView(this).apply {
                            text = "Aucun fichier pour ce job"
                            setTextColor(MUTED)
                        })
                    }
                    for (index in 0 until files.length()) {
                        val file = files.getJSONObject(index)
                        val id = file.getString("id")
                        filesContainer.addView(actionButton("${file.optString("kind")} · ${file.optString("relative_path")}") {
                            selectedFileId = id
                            selectedFileName = safeDownloadName(file.optString("relative_path"))
                            selectedFileMime = mimeTypeFor(selectedFileName.orEmpty())
                            selectedFileLabel.text = "Fichier sélectionné : $selectedFileName"
                        })
                    }
                    show("${files.length()} fichier(s) chargé(s)")
                }
            } catch (error: Exception) {
                show("Chargement fichiers échoué : ${error.message ?: "erreur réseau"}")
            }
        }
    }

    private fun uploadSelected() {
        val jobId = selectedJobId ?: run { show("Sélectionnez d'abord un job"); return }
        val uri = selectedUploadUri ?: run { show("Choisissez d'abord un fichier"); return }
        val name = selectedUploadName ?: run { show("Choisissez d'abord un fichier"); return }
        val declaredSize = selectedUploadSize
        executor.execute {
            try {
                if (declaredSize > ApiClient.MAX_UPLOAD_BYTES) {
                    show("Le fichier dépasse la limite ${formatSize(ApiClient.MAX_UPLOAD_BYTES)}")
                    return@execute
                }
                show("Lecture de $name…")
                val content = readUpload(uri, declaredSize)
                show("Upload de $name (${formatSize(content.size.toLong())})…")
                val result = client.uploadInput(server(), normalToken(), jobId, name, content)
                if (result.code in 200..299) show("Fichier envoyé : $name") else show("Upload HTTP ${result.code}: ${result.body.take(500)}")
            } catch (error: Exception) {
                show("Upload failed: ${error.message ?: "unable to read file"}")
            }
        }
    }

    private fun readUpload(uri: Uri, declaredSize: Long): ByteArray {
        if (declaredSize > ApiClient.MAX_UPLOAD_BYTES) throw IllegalArgumentException("file exceeds 10 MiB client limit")
        val initialSize = if (declaredSize in 0L..Int.MAX_VALUE.toLong()) declaredSize.toInt() else 32 * 1024
        val output = ByteArrayOutputStream(initialSize)
        val buffer = ByteArray(16 * 1024)
        var total = 0L
        val input = contentResolver.openInputStream(uri) ?: throw IllegalArgumentException("file cannot be opened")
        input.use {
            while (true) {
                val count = it.read(buffer)
                if (count <= 0) break
                total += count
                if (total > ApiClient.MAX_UPLOAD_BYTES) throw IllegalArgumentException("file exceeds 10 MiB client limit")
                output.write(buffer, 0, count)
            }
        }
        return output.toByteArray()
    }

    private fun safeDownloadName(relativePath: String): String {
        val candidate = relativePath.substringAfterLast('/').substringAfterLast('\\')
            .replace(Regex("[^A-Za-z0-9._-]"), "_")
            .take(120)
        return candidate.ifBlank { "omp-remote-download.bin" }
    }

    private fun mimeTypeFor(name: String): String {
        val extension = name.substringAfterLast('.', "").lowercase(Locale.ROOT)
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension)
            ?: "application/octet-stream"
    }

    private fun downloadSelected() {
        val jobId = selectedJobId ?: run { show("Sélectionnez d'abord un job"); return }
        val fileId = selectedFileId ?: run { show("Sélectionnez d'abord un fichier"); return }
        executor.execute {
            try {
                val result = client.downloadFile(server(), normalToken(), jobId, fileId)
                if (result.code !in 200..299) {
                    show("Téléchargement HTTP ${result.code}")
                    return@execute
                }
                pendingDownloadBytes = result.bytes
                pendingDownloadName = selectedFileName ?: "omp-remote-download.bin"
                pendingDownloadMime = selectedFileMime
                runOnUiThread {
                    if (!activityActive) return@runOnUiThread
                    show("Choisissez l'emplacement d'enregistrement")
                    try {
                        startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                            addCategory(Intent.CATEGORY_OPENABLE)
                            type = pendingDownloadMime
                            putExtra(Intent.EXTRA_TITLE, pendingDownloadName)
                        }, SAVE_DOWNLOAD_REQUEST)
                    } catch (error: Exception) {
                        pendingDownloadBytes = null
                        show("Sélecteur d'enregistrement indisponible : ${error.message ?: "erreur"}")
                    }
                }
            } catch (error: Exception) {
                show("Téléchargement échoué : ${error.message ?: "erreur réseau"}")
            }
        }
    }

    private fun checkForUpdate() {
        executor.execute {
            try {
                show("Recherche d'une mise à jour authentifiée…")
                val metadataResult = client.checkUpdate(server(), normalToken())
                if (metadataResult.code !in 200..299) {
                    show("Update check HTTP ${metadataResult.code}: ${metadataResult.body.take(500)}")
                    return@execute
                }
                val update = JSONObject(metadataResult.body).getJSONObject("update")
                val versionCode = update.optLong("version_code", -1L)
                val versionName = update.optString("version_name").trim()
                val expectedSize = update.optLong("size_bytes", -1L)
                val filename = update.optString("filename", "omp-remote-v$versionName.apk")
                require(versionCode >= 0L && versionName.isNotBlank() && expectedSize >= 0L) { "invalid update metadata" }
                runOnUiThread {
                    if (activityActive) showUpdateDialog(versionName, versionCode, expectedSize, filename)
                }
            } catch (error: Exception) {
                show("Update failed: ${error.message ?: "network error"}")
            }
        }
    }

    private fun showUpdateDialog(versionName: String, versionCode: Long, expectedSize: Long, filename: String) {
        val newer = versionCode > BuildConfig.VERSION_CODE.toLong()
        val message = "Version installée : ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})\n" +
            "Version disponible : $versionName ($versionCode)\n" +
            "Fichier : $filename\n" +
            "Taille : ${formatSize(expectedSize)}\n\n" +
            if (newer) "Télécharger OMP Remote v$versionName ?" else "Aucune version plus récente n'est disponible."
        val builder = AlertDialog.Builder(this).setTitle("Mise à jour OMP Remote").setMessage(message)
        if (newer) {
            builder.setNegativeButton("Plus tard", null)
                .setPositiveButton("Télécharger OMP Remote v$versionName") { _, _ -> downloadAndInstallUpdate(versionName, versionCode, expectedSize) }
        } else {
            builder.setPositiveButton("Fermer", null)
        }
        builder.show()
        show(if (newer) "Version disponible : $versionName" else "Application à jour : ${BuildConfig.VERSION_NAME}")
    }

    private fun downloadAndInstallUpdate(versionName: String, versionCode: Long, expectedSize: Long) {
        executor.execute {
            try {
                show("Téléchargement vérifié de OMP Remote v$versionName…")
                val metadataResult = client.checkUpdate(server(), normalToken())
                if (metadataResult.code !in 200..299) {
                    show("Update check HTTP ${metadataResult.code}: ${metadataResult.body.take(500)}")
                    return@execute
                }
                val update = JSONObject(metadataResult.body).getJSONObject("update")
                val refreshedCode = update.optLong("version_code", -1L)
                val refreshedName = update.optString("version_name").trim()
                val refreshedSize = update.optLong("size_bytes", -1L)
                require(refreshedCode == versionCode && refreshedName == versionName && refreshedSize == expectedSize) {
                    "update metadata changed during download"
                }
                val expectedSha = update.optString("sha256").trim().lowercase(Locale.ROOT)
                require(expectedSha.matches(Regex("[0-9a-f]{64}"))) { "update metadata has no valid SHA-256" }
                val download = client.downloadUpdate(server(), normalToken())
                if (download.code !in 200..299) {
                    show("Update download HTTP ${download.code}: ${download.body.take(500)}")
                    return@execute
                }
                require(download.bytes.size.toLong() == expectedSize) { "update size does not match metadata" }
                val actual = MessageDigest.getInstance("SHA-256")
                    .digest(download.bytes)
                    .joinToString("") { "%02x".format(Locale.US, it.toInt() and 0xff) }
                require(actual == expectedSha) { "update integrity check failed" }
                val safeName = "${UUID.randomUUID()}.apk"
                cacheDir.resolve(safeName).writeBytes(download.bytes)
                runOnUiThread {
                    if (!activityActive) return@runOnUiThread
                    confirmInstallUpdate(
                        Uri.parse("content://${packageName}.files/$safeName"),
                        versionName,
                        versionCode,
                        download.bytes.size,
                    )
                }
            } catch (error: Exception) {
                show("Update failed: ${error.message ?: "network error"}")
            }
        }
    }

    private fun confirmInstallUpdate(uri: Uri, versionName: String, versionCode: Long, size: Int) {
        AlertDialog.Builder(this)
            .setTitle("Installer OMP Remote v$versionName ?")
            .setMessage("Version installée : ${BuildConfig.VERSION_NAME}\nVersion disponible : $versionName ($versionCode)\nTaille vérifiée : ${formatSize(size.toLong())}.\nAndroid demandera la confirmation finale.")
            .setNegativeButton("Pas maintenant", null)
            .setPositiveButton("Installer") { _, _ ->
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
                    show("Autorisez l'installation des mises à jour, puis relancez la recherche")
                    try {
                        startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES).apply { data = Uri.parse("package:$packageName") })
                    } catch (error: Exception) {
                        show("Could not open install permission settings: ${error.message ?: "settings unavailable"}")
                    }
                } else {
                    val install = Intent(Intent.ACTION_VIEW).apply {
                        setDataAndType(uri, "application/vnd.android.package-archive")
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    }
                    try {
                        startActivity(install)
                        show("Ouverture de l'installateur pour OMP Remote v$versionName")
                    } catch (error: Exception) {
                        show("Could not open Android installer: ${error.message ?: "no installer"}")
                    }
                }
            }
            .show()
    }

    private fun confirmEmergencyKill() {
        AlertDialog.Builder(this)
            .setTitle("KILL OMP REMOTE ?")
            .setMessage("Portée réelle : désactive OMP Remote, arrête ses workers et son API, puis conserve le statut emergency. Les sites, VPN, SSH, Docker, Minecraft, Glances et les processus manuels hors OMP Remote ne sont pas ciblés. La réactivation est locale et root uniquement.")
            .setNegativeButton("Annuler", null)
            .setPositiveButton("KILL") { _, _ -> runEmergency { client.emergencyKill(server(), emergencyToken()) } }
            .show()
    }
}
