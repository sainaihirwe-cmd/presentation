import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, BackHandler, Linking, Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';
import * as SecureStore from 'expo-secure-store';
import * as Sharing from 'expo-sharing';
import { Directory, File, Paths } from 'expo-file-system';

// SlideGen runs on a server (Render); this app shows it full screen on the phone.
const DEFAULT_URL = 'https://slidegen.onrender.com';
const PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';

const C = { bg: '#0b1020', card: '#151c33', line: '#2d3758', text: '#e5e9f5', muted: '#a3acc8', accent: '#8b5cf6' };

export default function App() {
  const [settings, setSettings] = useState(null); // {url, password} once loaded
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    (async () => {
      const url = await SecureStore.getItemAsync('url');
      const password = (await SecureStore.getItemAsync('password')) || '';
      setSettings({ url: url || '', password });
      setEditing(!url);
    })();
  }, []);

  const save = async (url, password) => {
    await SecureStore.setItemAsync('url', url);
    await SecureStore.setItemAsync('password', password);
    setSettings({ url, password });
    setEditing(false);
  };

  return (
    <SafeAreaProvider>
      <StatusBar style="light" />
      <SafeAreaView style={styles.screen}>
        {!settings ? (
          <ActivityIndicator color={C.accent} style={{ flex: 1 }} />
        ) : editing ? (
          <Setup initial={settings} onSave={save} />
        ) : (
          <SlideGen {...settings} onChangeAddress={() => setEditing(true)} />
        )}
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

function Setup({ initial, onSave }) {
  const [url, setUrl] = useState(initial.url || DEFAULT_URL);
  const [password, setPassword] = useState(initial.password);
  const [status, setStatus] = useState('');

  const connect = async () => {
    const base = url.trim().replace(/\/+$/, '');
    if (!/^https?:\/\//.test(base)) {
      setStatus('The address must start with https://');
      return;
    }
    setStatus('Connecting… a sleeping free server can take up to a minute to wake.');
    try {
      // the app manifest is reachable without the site password, so this only checks the address
      const res = await fetch(base + '/manifest.webmanifest');
      if (!res.ok) throw new Error(`the server answered ${res.status}`);
      await onSave(base, password);
    } catch (e) {
      setStatus(`Could not reach SlideGen at that address (${e.message}). Check it and your internet.`);
    }
  };

  return (
    <View style={styles.setup}>
      <Text style={styles.logo}>▣ SlideGen</Text>
      <Text style={styles.lead}>Enter the web address where SlideGen is running, for example your Render site.</Text>
      <Text style={styles.label}>SlideGen address</Text>
      <TextInput style={styles.input} value={url} onChangeText={setUrl} autoCapitalize="none"
        autoCorrect={false} keyboardType="url" placeholder={DEFAULT_URL} placeholderTextColor={C.muted} />
      <Text style={styles.label}>Site password (only if you set SITE_PASSWORD)</Text>
      <TextInput style={styles.input} value={password} onChangeText={setPassword}
        secureTextEntry autoCapitalize="none" placeholder="Leave empty if none" placeholderTextColor={C.muted} />
      <Pressable style={styles.button} onPress={connect}>
        <Text style={styles.buttonText}>Open SlideGen</Text>
      </Pressable>
      {!!status && <Text style={styles.status}>{status}</Text>}
    </View>
  );
}

function SlideGen({ url, password, onChangeAddress }) {
  const web = useRef(null);
  const [canGoBack, setCanGoBack] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const auth = password ? { username: 'slidegen', password } : undefined;

  // Android Back goes back inside SlideGen before leaving the app
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (canGoBack) {
        web.current?.goBack();
        return true;
      }
      return false;
    });
    return () => sub.remove();
  }, [canGoBack]);

  // .pptx downloads: fetch the file, then let the phone save, open or send it
  const download = useCallback(async (fileUrl) => {
    setBusy('Preparing your PowerPoint…');
    try {
      const folder = new Directory(Paths.cache, 'downloads');
      folder.create({ idempotent: true, intermediates: true });
      const headers = password ? { Authorization: 'Basic ' + btoa(`slidegen:${password}`) } : undefined;
      const file = await File.downloadFileAsync(fileUrl, folder, { headers, idempotent: true });
      await Sharing.shareAsync(file.uri, { mimeType: PPTX, dialogTitle: 'Save or open your presentation' });
    } catch (e) {
      setError(`Could not download the file: ${e.message}`);
    } finally {
      setBusy('');
    }
  }, [password]);

  const onRequest = useCallback((req) => {
    if (req.url.startsWith(url + '/download/')) {
      download(req.url);
      return false;
    }
    if (!req.url.startsWith(url) && /^https?:/.test(req.url)) {
      Linking.openURL(req.url); // YouTube, Wikipedia credits... open in the browser
      return false;
    }
    return true;
  }, [url, download]);

  if (error) {
    return (
      <View style={styles.setup}>
        <Text style={styles.logo}>▣ SlideGen</Text>
        <Text style={styles.lead}>{error}</Text>
        <Pressable style={styles.button} onPress={() => setError('')}>
          <Text style={styles.buttonText}>Try again</Text>
        </Pressable>
        <Pressable style={styles.link} onPress={onChangeAddress}>
          <Text style={styles.linkText}>Change SlideGen address</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={{ flex: 1 }}>
      <WebView
        ref={web}
        source={{ uri: url + '/' }}
        basicAuthCredential={auth}
        style={{ backgroundColor: C.bg }}
        onShouldStartLoadWithRequest={onRequest}
        onNavigationStateChange={(s) => setCanGoBack(s.canGoBack)}
        onError={(e) => setError(`SlideGen did not load (${e.nativeEvent.description}). Check your internet.`)}
        onHttpError={(e) => e.nativeEvent.statusCode === 401 &&
          setError('The site password is wrong. Change it under "Change SlideGen address".')}
        startInLoadingState
        renderLoading={() => (
          <View style={styles.loading}>
            <ActivityIndicator size="large" color={C.accent} />
            <Text style={styles.status}>Loading SlideGen… a sleeping free server can take up to a minute.</Text>
          </View>
        )}
        allowsFullscreenVideo
        mediaPlaybackRequiresUserAction={false}
        domStorageEnabled
      />
      {!!busy && (
        <View style={styles.overlay}>
          <ActivityIndicator size="large" color={C.accent} />
          <Text style={styles.status}>{busy}</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg },
  setup: { flex: 1, padding: 24, justifyContent: 'center' },
  logo: { color: C.text, fontSize: 28, fontWeight: '700', marginBottom: 12 },
  lead: { color: C.muted, fontSize: 16, lineHeight: 23, marginBottom: 24 },
  label: { color: C.text, fontSize: 14, fontWeight: '600', marginBottom: 6 },
  input: {
    backgroundColor: C.card, borderColor: C.line, borderWidth: 1, borderRadius: 10,
    color: C.text, fontSize: 16, paddingHorizontal: 14, paddingVertical: 12, marginBottom: 18,
  },
  button: { backgroundColor: '#7c3aed', borderRadius: 12, paddingVertical: 15, alignItems: 'center', marginTop: 6 },
  buttonText: { color: '#fff', fontSize: 17, fontWeight: '700' },
  link: { alignItems: 'center', padding: 16 },
  linkText: { color: C.accent, fontSize: 15 },
  status: { color: C.muted, fontSize: 14, lineHeight: 20, marginTop: 16, textAlign: 'center' },
  loading: { ...StyleSheet.absoluteFillObject, backgroundColor: C.bg, alignItems: 'center', justifyContent: 'center', padding: 24 },
  overlay: {
    ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(11,16,32,0.85)',
    alignItems: 'center', justifyContent: 'center', padding: 24,
  },
});
