package com.universalmediasaver.test;

import android.Manifest;
import android.app.DownloadManager;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.view.View;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends android.app.Activity {

    private static final String API = "https://universal-media-saver-production.up.railway.app/";
    private final ExecutorService executor = Executors.newCachedThreadPool();
    private TextView statusText, pasteButton, logText, progressText;
    private ProgressBar progressBar;
    private ScrollView logScroll;
    private volatile boolean busy = false;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        statusText = findViewById(R.id.statusText);
        pasteButton = findViewById(R.id.pasteButton);
        logText = findViewById(R.id.logText);
        progressText = findViewById(R.id.progressText);
        progressBar = findViewById(R.id.progressBar);
        logScroll = findViewById(R.id.logScroll);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 100);
        }
        pasteButton.setOnClickListener(v -> startFromClipboard());
        appendLog("Backend: " + API);
        appendLog("Tap orb → clipboard → normalize → extract → download.");
    }

    private void startFromClipboard() {
        if (busy) { Toast.makeText(this, "Download already running", Toast.LENGTH_SHORT).show(); return; }
        ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (cm == null || !cm.hasPrimaryClip()) { appendLog("ERROR: Clipboard is empty."); return; }
        ClipData clip = cm.getPrimaryClip();
        if (clip == null || clip.getItemCount() == 0) return;
        CharSequence cs = clip.getItemAt(0).coerceToText(this);
        String raw = cs == null ? "" : cs.toString().trim();
        String normalized = normalizeUrl(raw);
        if (normalized == null) { appendLog("ERROR: No valid link found."); status("Invalid clipboard link"); return; }
        setProgress(0); busy = true; pasteButton.setAlpha(.45f); status("Finding video…");
        appendLog("Clipboard: " + raw); appendLog("Normalized: " + normalized);
        executor.execute(() -> extractAndDownload(normalized));
    }

    private String normalizeUrl(String raw) {
        if (raw == null) return null;
        String s = raw.trim().replace("\u200B", "");
        Matcher md = Pattern.compile("\\[[^\\]]*\\]\\((https?://[^)\\s]+)\\)", Pattern.CASE_INSENSITIVE).matcher(s);
        if (md.find()) s = md.group(1);
        Matcher full = Pattern.compile("https?://[^\\s)\\]}>\"']+", Pattern.CASE_INSENSITIVE).matcher(s);
        if (full.find()) s = full.group(); else {
            Matcher domain = Pattern.compile("(?:www\\.)?[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}(?:/[^\\s)\\]}>\"']*)?").matcher(s);
            if (!domain.find()) return null;
            s = "https://" + domain.group();
        }
        while (s.endsWith(".") || s.endsWith(",") || s.endsWith(";") || s.endsWith(")")) s = s.substring(0, s.length()-1);
        if (!s.startsWith("http://") && !s.startsWith("https://")) s = "https://" + s.replaceFirst("^/+", "");
        try { URI uri = URI.create(s); return uri.getHost() == null ? null : uri.toString(); } catch (Exception e) { return null; }
    }

    private void extractAndDownload(String pageUrl) {
        HttpURLConnection c = null;
        try {
            appendLog("POST / → extracting media");
            c = (HttpURLConnection) new URL(API).openConnection();
            c.setConnectTimeout(20000); c.setReadTimeout(45000); c.setRequestMethod("POST");
            c.setRequestProperty("Content-Type", "application/json"); c.setRequestProperty("Accept", "application/json"); c.setDoOutput(true);
            JSONObject body = new JSONObject(); body.put("url", pageUrl); body.put("videoQuality", "max"); body.put("downloadMode", "auto");
            c.getOutputStream().write(body.toString().getBytes(StandardCharsets.UTF_8));
            int code = c.getResponseCode(); String text = readText(code >= 400 ? c.getErrorStream() : c.getInputStream());
            appendLog("Backend HTTP " + code); appendLog("Response: " + trimForLog(text));
            JSONObject json = new JSONObject(text);
            if (code >= 400) { fail(extractError(json, "Backend extraction failed")); return; }
            String st = json.optString("status", "");
            if ("redirect".equalsIgnoreCase(st) || "tunnel".equalsIgnoreCase(st)) {
                String media = json.optString("url", ""); String filename = sanitizeFilename(json.optString("filename", "download.mp4"));
                if (media.isEmpty()) { fail("No media URL returned."); return; }
                appendLog("Media found: " + media); downloadMedia(media, filename, json.optJSONObject("headers")); return;
            }
            if ("picker".equalsIgnoreCase(st)) {
                JSONArray p = json.optJSONArray("picker"); if (p == null || p.length() == 0) { fail("Picker empty."); return; }
                JSONObject first = p.getJSONObject(0); downloadMedia(first.optString("url"), sanitizeFilename(first.optString("filename", "download.mp4")), null); return;
            }
            if ("error".equalsIgnoreCase(st)) { fail(extractError(json, "Extractor error")); return; }
            fail("Unsupported backend response: " + st);
        } catch (Exception e) { fail(e.getClass().getSimpleName() + ": " + e.getMessage()); }
        finally { if (c != null) c.disconnect(); }
    }

    private void downloadMedia(String mediaUrl, String filename, JSONObject headers) {
        if (mediaUrl.contains(".m3u8")) { appendLog("HLS detected. Downloading segments."); downloadHls(mediaUrl, filename, headers); }
        else { appendLog("Direct media detected. Starting DownloadManager."); startDownloadManager(mediaUrl, filename, headers); }
    }

    private void startDownloadManager(String mediaUrl, String filename, JSONObject headers) {
        try {
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(mediaUrl));
            r.setTitle(filename); r.setDescription("Universal Media Saver test download");
            r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setAllowedOverMetered(true); r.setAllowedOverRoaming(true); r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename);
            if (headers != null) { Iterator<String> it = headers.keys(); while (it.hasNext()) { String k=it.next(), v=headers.optString(k,""); if(!v.isEmpty()) r.addRequestHeader(k,v); } }
            DownloadManager dm = (DownloadManager)getSystemService(DOWNLOAD_SERVICE); long id=dm.enqueue(r); appendLog("Download ID: " + id);
            executor.execute(() -> monitorDownload(dm,id,filename));
        } catch (Exception e) { fail("Download start failed: " + e.getMessage()); }
    }

    private void monitorDownload(DownloadManager dm, long id, String filename) {
        boolean done=false;
        while(!done) try {
            Cursor cur=dm.query(new DownloadManager.Query().setFilterById(id));
            if(cur!=null && cur.moveToFirst()) {
                int st=cur.getInt(cur.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
                long sofar=cur.getLong(cur.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR));
                long total=cur.getLong(cur.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES)); setProgress(total>0?(int)Math.min(100,sofar*100L/total):0);
                if(st==DownloadManager.STATUS_SUCCESSFUL){done=true; complete("Saved: Downloads/"+filename);} else if(st==DownloadManager.STATUS_FAILED){int reason=cur.getInt(cur.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON));done=true;fail("Download failed. Reason="+reason);}
            }
            if(cur!=null)cur.close(); if(!done)Thread.sleep(800);
        } catch(Exception e){fail("Progress failed: "+e.getMessage());done=true;}
    }

    private void downloadHls(String playlistUrl, String filename, JSONObject headers) {
        try {
            String master=httpGetText(playlistUrl,headers), mediaPlaylistUrl=playlistUrl; String[] lines=master.split("\\r?\\n");
            long bestBw=-1; String bestVariant=null;
            for(int i=0;i<lines.length;i++) if(lines[i].trim().startsWith("#EXT-X-STREAM-INF:")) {
                long bw=parseBandwidth(lines[i]);
                for(int j=i+1;j<lines.length;j++){String candidate=lines[j].trim();if(!candidate.isEmpty()&&!candidate.startsWith("#")){if(bw>=bestBw){bestBw=bw;bestVariant=resolveUrl(playlistUrl,candidate);}break;}}
            }
            if(bestVariant!=null){mediaPlaylistUrl=bestVariant;appendLog("HLS variant bandwidth="+bestBw);master=httpGetText(mediaPlaylistUrl,headers);lines=master.split("\\r?\\n");}
            List<String> parts=new ArrayList<>(); String initPart=null;
            for(String line:lines){String s=line.trim();if(s.startsWith("#EXT-X-MAP:")){Matcher m=Pattern.compile("URI=\"([^\"]+)\"").matcher(s);if(m.find())initPart=resolveUrl(mediaPlaylistUrl,m.group(1));}else if(!s.isEmpty()&&!s.startsWith("#"))parts.add(resolveUrl(mediaPlaylistUrl,s));}
            if(parts.isEmpty()){fail("HLS playlist has no segments.");return;}
            File dir=Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);if(!dir.exists())dir.mkdirs();
            if(!filename.toLowerCase(Locale.US).endsWith(".mp4")&&!filename.toLowerCase(Locale.US).endsWith(".ts"))filename+=".mp4";
            File out=uniqueFile(dir,filename);appendLog("Segments: "+parts.size());appendLog("Saving: "+out.getAbsolutePath());
            try(FileOutputStream fos=new FileOutputStream(out)){if(initPart!=null)copyUrl(initPart,headers,fos);for(int i=0;i<parts.size();i++){copyUrl(parts.get(i),headers,fos);setProgress((int)(((i+1)*100L)/parts.size()));if(i==0||(i+1)%10==0||i+1==parts.size())appendLog("Segment "+(i+1)+"/"+parts.size());}}
            complete("Saved: "+out.getAbsolutePath());
        } catch(Exception e){fail("HLS download failed: "+e.getMessage());}
    }

    private void copyUrl(String url, JSONObject headers, FileOutputStream fos) throws Exception { HttpURLConnection c=openGet(url,headers);try(InputStream in=new BufferedInputStream(c.getInputStream())){byte[] buf=new byte[65536];int n;while((n=in.read(buf))!=-1)fos.write(buf,0,n);}finally{c.disconnect();} }
    private String httpGetText(String url, JSONObject headers) throws Exception { HttpURLConnection c=openGet(url,headers);try{return readText(c.getInputStream());}finally{c.disconnect();} }
    private HttpURLConnection openGet(String url, JSONObject headers) throws Exception { HttpURLConnection c=(HttpURLConnection)new URL(url).openConnection();c.setConnectTimeout(20000);c.setReadTimeout(45000);c.setInstanceFollowRedirects(true);c.setRequestProperty("Accept","*/*");if(headers!=null){Iterator<String>it=headers.keys();while(it.hasNext()){String k=it.next(),v=headers.optString(k,"");if(!v.isEmpty())c.setRequestProperty(k,v);}}int code=c.getResponseCode();if(code>=400)throw new RuntimeException("HTTP "+code+" for media URL");return c; }
    private long parseBandwidth(String s){Matcher m=Pattern.compile("BANDWIDTH=(\\d+)").matcher(s);if(m.find())try{return Long.parseLong(m.group(1));}catch(Exception ignored){}return 0;}
    private String resolveUrl(String base,String child)throws Exception{return new URL(new URL(base),child).toString();}
    private File uniqueFile(File dir,String name){File f=new File(dir,name);if(!f.exists())return f;String base=name,ext="";int dot=name.lastIndexOf('.');if(dot>0){base=name.substring(0,dot);ext=name.substring(dot);}for(int i=1;i<10000;i++){f=new File(dir,base+" ("+i+")"+ext);if(!f.exists())return f;}return new File(dir,System.currentTimeMillis()+"_"+name);}
    private String sanitizeFilename(String s){if(s==null||s.trim().isEmpty())return "download.mp4";s=s.replaceAll("[\\\\/:*?\"<>|]","_").trim();if(s.length()>120)s=s.substring(0,120);return s;}
    private String extractError(JSONObject json,String fallback){try{JSONObject e=json.optJSONObject("error");if(e!=null)return e.optString("message",fallback);JSONObject d=json.optJSONObject("detail");if(d!=null)return d.optString("message",fallback);String x=json.optString("detail","");if(!x.isEmpty())return x;}catch(Exception ignored){}return fallback;}
    private String readText(InputStream in)throws Exception{if(in==null)return "";BufferedReader br=new BufferedReader(new InputStreamReader(in,StandardCharsets.UTF_8));StringBuilder sb=new StringBuilder();String line;while((line=br.readLine())!=null)sb.append(line).append('\n');return sb.toString().trim();}
    private String trimForLog(String s){if(s==null)return "";return s.length()>900?s.substring(0,900)+"…":s;}
    private void setProgress(int p){runOnUiThread(()->{progressBar.setProgress(p);progressText.setText(p+"%");});}
    private void status(String s){runOnUiThread(()->statusText.setText(s));}
    private void appendLog(String s){runOnUiThread(()->{logText.append("\n"+s);logScroll.post(()->logScroll.fullScroll(View.FOCUS_DOWN));});}
    private void complete(String msg){setProgress(100);appendLog("DONE: "+msg);runOnUiThread(()->{statusText.setText("Download complete");pasteButton.setAlpha(1f);busy=false;Toast.makeText(this,"Download complete",Toast.LENGTH_SHORT).show();});}
    private void fail(String msg){appendLog("ERROR: "+msg);runOnUiThread(()->{statusText.setText("Failed — check log");pasteButton.setAlpha(1f);busy=false;});}
}
