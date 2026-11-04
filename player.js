/* Bundled HLS.js handles HLS on browsers without native HLS support. */
let activeHls = null;
let playbackGeneration = 0;
function stopPlayback() {
    playbackGeneration++;
    const video = document.getElementById('video');
    if (activeHls) { activeHls.destroy(); activeHls = null; }
    video.pause();
    video.removeAttribute('src');
    video.load();
    video.onerror = null;
    document.getElementById('player-panel').hidden = true;
}
function playEntry(entry) {
    stopPlayback();
    const generation = playbackGeneration;
    const video = document.getElementById('video');
    const panel = document.getElementById('player-panel');
    const message = document.getElementById('player-message');
    panel.hidden = false;
    document.getElementById('playing-title').textContent = entry.title;
    message.textContent = 'Loading…';
    panel.scrollIntoView({behavior: 'smooth', block: 'start'});
    const source = entry.url.split('|')[0].trim();
    let url;
    try {
        url = new URL(source);
        if (!['http:', 'https:'].includes(url.protocol)) throw Error();
    } catch (_) {
        message.textContent = 'Playback requires a valid HTTP or HTTPS media URL.';
        return;
    }
    const fail = () => {
        if (generation !== playbackGeneration) return;
        message.textContent = 'Unable to play this media. The link may be unavailable, require provider headers, or use a codec your browser cannot decode. HLS providers must allow browser access (CORS). Try the same link in VLC.';
    };
    const begin = () => {
        if (generation !== playbackGeneration) return;
        video.play().then(() => {
            if (generation === playbackGeneration) message.textContent = 'Playing';
        }).catch(error => {
            if (generation !== playbackGeneration) return;
            if (error.name === 'NotAllowedError') message.textContent = 'Press Play in the video controls to begin.';
            else if (error.name !== 'AbortError') fail();
        });
    };
    video.onerror = fail;
    const hls = /\.m3u8?$/i.test(url.pathname) || /mpegurl|hls/i.test(entry.metadata?.['stream-type'] || '');
    if (hls && !video.canPlayType('application/vnd.apple.mpegurl')) {
        if (!window.Hls || !Hls.isSupported()) {
            message.textContent = 'This browser does not support HLS playback. Try a current Safari, Chrome, Firefox or Edge browser.';
            return;
        }
        activeHls = new Hls();
        activeHls.on(Hls.Events.MANIFEST_PARSED, begin);
        activeHls.on(Hls.Events.ERROR, (_, data) => {
            if (data.fatal && generation === playbackGeneration) {
                activeHls.destroy(); activeHls = null; fail();
            }
        });
        activeHls.loadSource(source);
        activeHls.attachMedia(video);
    } else {
        video.src = source;
        begin();
    }
}
document.getElementById('stop-playback').onclick = stopPlayback;
