// Run with node tests/test_player.cjs; no npm dependencies required.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
let plays = 0, destroyed = 0, current;
const video = {pause(){},load(){},removeAttribute(){delete this.src},canPlayType(){return ''},play(){plays++;return Promise.resolve()}};
const elements = {'video':video,'player-panel':{hidden:true,scrollIntoView(){}},'playing-title':{},'player-message':{},'stop-playback':{}};
class Hls {
    static Events = {MANIFEST_PARSED:'ready',ERROR:'error'};
    static isSupported(){return true}
    constructor(){this.events={};current=this}
    on(event,fn){this.events[event]=fn}
    loadSource(src){this.source=src}
    attachMedia(media){this.media=media}
    destroy(){destroyed++}
}
const context = vm.createContext({document:{getElementById:id=>elements[id]},URL,Hls,window:{Hls}});
vm.runInContext(fs.readFileSync('player.js','utf8'),context);
const play = entry=>context.playEntry({title:'Example',metadata:{},...entry});
play({url:'https://example.com/movie.mp4'});
assert.equal(video.src,'https://example.com/movie.mp4');
assert.equal(plays,1);
play({url:'https://example.com/live.m3u8'});
assert.equal(current.source,'https://example.com/live.m3u8');
assert.equal(current.media,video);
current.events.ready();
assert.equal(plays,2);
current.events.error(null,{fatal:true});
assert.match(elements['player-message'].textContent,/Unable to play/);
assert.equal(destroyed,1);
play({url:'https://example.com/live',metadata:{'stream-type':'hls'}});
assert.equal(current.source,'https://example.com/live');
elements['stop-playback'].onclick();
assert.equal(destroyed,2);
assert.equal(elements['player-panel'].hidden,true);
play({url:'file:///etc/passwd'});
assert.match(elements['player-message'].textContent,/HTTP or HTTPS/);
console.log('Player checks passed: movie playback, HLS, errors, switching, close, invalid URLs.');
