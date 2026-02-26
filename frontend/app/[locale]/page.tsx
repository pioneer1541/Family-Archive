import {redirect} from 'next/navigation';

export default function LocaleIndexPage({params}: {params: {locale: string}}) {
  redirect(`/${params.locale}/dashboard`);
}
