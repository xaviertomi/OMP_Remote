package com.ompremote

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import java.io.File

class CacheFileProvider : ContentProvider() {
    override fun onCreate(): Boolean = true

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        require(mode == "r") { "read-only provider" }
        val file = safeFile(uri)
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    }

    override fun getType(uri: Uri): String =
        if (uri.lastPathSegment?.endsWith(".apk") == true) {
            "application/vnd.android.package-archive"
        } else {
            "application/octet-stream"
        }

    override fun query(
        uri: Uri,
        projection: Array<out String>?,
        selection: String?,
        selectionArgs: Array<out String>?,
        sortOrder: String?,
    ): Cursor {
        val file = safeFile(uri)
        val columns = projection ?: arrayOf("_display_name", "_size")
        return MatrixCursor(columns).apply {
            addRow(columns.map { column ->
                when (column) {
                    "_display_name" -> file.name
                    "_size" -> file.length()
                    else -> null
                }
            }.toTypedArray())
        }
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0

    private fun safeFile(uri: Uri): File {
        val name = uri.pathSegments.singleOrNull() ?: error("invalid file URI")
        require(name.matches(Regex("[a-fA-F0-9-]{36}\\.(bin|apk)"))) { "invalid file name" }
        val root = requireNotNull(context).cacheDir.canonicalFile
        val file = File(root, name).canonicalFile
        require(file.parentFile == root && file.isFile) { "file not found" }
        return file
    }
}
