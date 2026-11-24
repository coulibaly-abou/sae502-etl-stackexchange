// SAÉ 5.02 : compare deux bases MongoDB document par document (lecture seule).
// Usage : mongosh --quiet verification_mongodb.js
//    ou, dans mongosh : load("verification_mongodb.js")
const origine = db.getSiblingDB("stack_exchange_3col");
const migree = db.getSiblingDB("stack_exchange_depuis_postgresql");
let erreurs = 0;

["users", "posts", "tags"].forEach(c => {
  const docsMigres = new Map(migree[c].find().toArray().map(d => [d._id, EJSON.stringify(d)]));
  const docsOrigine = origine[c].find().toArray();
  let absents = 0, differents = 0;
  docsOrigine.forEach(d => {
    const x = docsMigres.get(d._id);
    if (x === undefined) absents++;
    else if (x !== EJSON.stringify(d)) differents++;
  });
  const enTrop = docsMigres.size - (docsOrigine.length - absents);
  const ok = absents === 0 && differents === 0 && enTrop === 0;
  if (!ok) erreurs++;
  print(`${c.padEnd(6)} origine=${docsOrigine.length}  migree=${docsMigres.size}  absents=${absents}  differents=${differents}  en trop=${enTrop}  ${ok ? "OK" : "ERREUR"}`);
});

const nonValide = migree.posts.find({ $nor: [migree.getCollectionInfos({ name: "posts" })[0].options.validator] }).count();
print(`posts ne respectant pas le validateur : ${nonValide}  ${nonValide === 0 ? "OK" : "ERREUR"}`);
if (nonValide) erreurs++;

print(erreurs === 0 ? "\nMIGRATION POSTGRESQL -> MONGODB : AUCUNE ERREUR" : `\n${erreurs} CONTROLE(S) EN ERREUR`);
